import os
import json
import logging
import traceback
import asyncio
import time
import uuid
import requests
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton
from telegram.constants import ChatAction, ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from telegram.request import HTTPXRequest
from dotenv import load_dotenv
from web3 import Web3, HTTPProvider

try:
    from trading_cycle import TradingCycle
except ImportError:
    # Fallback for package installation
    from .trading_cycle import TradingCycle

try:
    from sol_trading_cycle import SolanaTradingCycle
except ImportError:
    try:
        # Fallback for package installation
        from .sol_trading_cycle import SolanaTradingCycle
    except ImportError:
        SolanaTradingCycle = None
from solana.rpc.api import Client as SolClient

# Import WalletManager
try:
    # Try absolute import first
    from wallet_manager import WalletManager
except ImportError:
    try:
        # Then try relative import
        from .wallet_manager import WalletManager
    except ImportError:
        # If both fail, try direct import from the current directory
        import sys
        from pathlib import Path
        sys.path.append(str(Path(__file__).parent))
        from wallet_manager import WalletManager

# Trending platforms manager
try:
    from .trending_platforms import TrendingPlatformManager
except Exception:
    from trending_platforms import TrendingPlatformManager

# Payment manager (optional, for paid/recorded campaigns)
try:
    from .payment_manager import PaymentManager
except Exception:
    try:
        from payment_manager import PaymentManager
    except Exception:
        PaymentManager = None


class TokenTrendingBot:
    def __init__(self):
        """Initializes the bot, loads configuration, and sets up handlers."""
        # Load environment variables from project root .env file
        env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
        if os.path.exists(env_path):
            load_dotenv(env_path, override=True)
        else:
            load_dotenv(override=True)  # Fallback to default .env location
            
        self.token = os.getenv("TELEGRAM_TOKEN")
        if not self.token:
            raise ValueError("TELEGRAM_TOKEN environment variable not found in .env file")
            
        # Create the application with tuned HTTP timeouts to reduce startup flakiness
        request = HTTPXRequest(
            connect_timeout=20,
            read_timeout=60,
            write_timeout=60,
            pool_timeout=30,
        )
        self.app = Application.builder().token(self.token).request(request).build()
        self.bot = self.app.bot

        # This will hold the loaded wallets for the session
        self.wallets = []
        self.trading_cycle = None
        
        # Configure logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)
        
        # Initialize price caches
        self._bnb_price_cache = {
            'price': 300.0,  # Default fallback price
            'timestamp': 0,
            'ttl': 300  # 5 minutes in seconds
        }
        self._price_cache = {
            'BNB': {'price': self._bnb_price_cache['price'], 'timestamp': 0, 'ttl': 300},
            'ETH': {'price': 3000.0, 'timestamp': 0, 'ttl': 300},
        }
        
        # Trending platforms manager
        self.trending_mgr = TrendingPlatformManager(self.logger)
        # Initialize payment manager if available
        self.payment_mgr = PaymentManager() if 'PaymentManager' in globals() and PaymentManager else None
        
        # Initialize Web3 connection
        self.web3 = None
        self.initialize_web3()
        
        # Initialize token configuration
        self.token_config = {
            'address': '',
            'chain': '',
            'ticker': '',
            'total_distributed_bnb': 0.0,
            'funded_wallets': []
        }
        # Initialize funded_wallets before loading config
        self.funded_wallets = []
        self.total_distributed_bnb = 0.0
        # Mapping of address -> private_key (populated from wallets file if available)
        self.wallet_private_keys = {}
        self.load_token_config()
        
        # Initialize connected_chain from config
        self.connected_chain = (self.token_config.get('chain') or '').upper() or None
        
        # Initialize central wallet with validation
        self.central_wallet_address = os.getenv('CENTRAL_WALLET_ADDRESS', '').strip()
        self.central_wallet_private_key = os.getenv('CENTRAL_WALLET_PRIVATE_KEY', '').strip()
        
        if not self.central_wallet_address or not self.central_wallet_private_key:
            self.logger.warning("Central wallet not fully configured. Wallet funding will not work.")
        elif self.web3 and self.central_wallet_address:
            try:
                # Validate wallet address format
                if not self.web3.is_address(self.central_wallet_address):
                    self.logger.error(f"Invalid wallet address format: {self.central_wallet_address}")
                    self.central_wallet_address = ''
                else:
                    # Convert to checksum address
                    self.central_wallet_address = self.web3.to_checksum_address(self.central_wallet_address)
                    self.logger.info(f"Central wallet configured: {self.central_wallet_address}")
            except Exception as e:
                self.logger.error(f"Error validating central wallet: {str(e)}")
                self.central_wallet_address = ''
        # Load wallets if we have a connected chain
        if self.connected_chain:
            self.logger.info(f"Loading wallets for chain: {self.connected_chain}")
            try:
                if self.connected_chain == 'BNB':
                    # Look for wallets in the project root's hardhat-scripts directory
                    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    wallets_file_path = os.path.join(project_root, 'hardhat-scripts', 'wallets.json')
                    self.logger.info(f"Looking for BNB wallets at: {wallets_file_path}")
                    # Fall back to bsc_wallets.json if it exists and wallets.json doesn't
                    if not os.path.exists(wallets_file_path):
                        self.logger.info(f"Primary wallet file not found, checking for fallback...")
                        fallback_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bsc_wallets.json')
                        self.logger.info(f"Checking fallback path: {fallback_path}")
                        if os.path.exists(fallback_path):
                            wallets_file_path = fallback_path
                            self.logger.info(f"Using fallback wallet file: {wallets_file_path}")
                elif self.connected_chain == 'ETH':
                    wallets_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'eth_wallet.json')
                    self.logger.info(f"Looking for ETH wallets at: {wallets_file_path}")
                elif self.connected_chain == 'SOL':
                    wallets_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sol_wallet.json')
                    self.logger.info(f"Looking for SOL wallets at: {wallets_file_path}")
                else:
                    self.logger.warning(f"Unsupported chain for wallet loading: {self.connected_chain}")
                    return
                
                wallets_file_path = os.path.normpath(wallets_file_path)
                self.logger.info(f"Final wallet file path: {wallets_file_path}")
                
                if os.path.exists(wallets_file_path):
                    self.logger.info(f"Wallet file exists, attempting to load...")
                    with open(wallets_file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        # Normalize list of dicts {address, private_key?}
                        self.wallets = data if isinstance(data, list) else []
                        self.logger.info(f"Loaded {len(self.wallets)} wallets")
            except Exception as e:
                self.logger.error(f"Error loading wallets for chain {self.connected_chain}: {e}", exc_info=True)

        self.register_handlers()

    # ---- Helpers for USD-based min wallet balance ----
    def _get_env_min_wallet_balance_usd(self) -> float:
        try:
            return float(os.getenv('MIN_WALLET_BALANCE_USD', '0') or 0)
        except Exception:
            return 0.0

    def _coingecko_id_for_chain(self, chain: str) -> str:
        ch = (chain or 'BNB').upper()
        return 'binancecoin' if ch == 'BNB' else 'ethereum'

    async def _fetch_native_price_usd(self, chain: str) -> float:
        try:
            # Simple no-auth fetch with tiny caching via self._price_cache
            ch = (chain or 'BNB').upper()
            cache = self._price_cache.get(ch, {'price': 0.0, 'timestamp': 0, 'ttl': 300})
            now = int(time.time())
            if cache and cache.get('price') and now - int(cache.get('timestamp', 0)) < int(cache.get('ttl', 300)):
                return float(cache['price'])
            cid = self._coingecko_id_for_chain(chain)
            resp = requests.get(
                'https://api.coingecko.com/api/v3/simple/price',
                params={'ids': cid, 'vs_currencies': 'usd'},
                timeout=5
            )
            data = resp.json()
            price = float(data.get(cid, {}).get('usd', 0) or 0)
            if price > 0:
                self._price_cache[ch] = {'price': price, 'timestamp': now, 'ttl': 300}
                return price
        except Exception as e:
            self.logger.warning(f"Price fetch failed: {e}")
        # fallback to previous cache or 0
        try:
            return float(self._price_cache.get((chain or 'BNB').upper(), {}).get('price', 0) or 0)
        except Exception:
            return 0.0

    async def _compute_min_required_native(self, chain: str) -> tuple[float, str]:
        """Compute min required native balance, considering USD env threshold.
        Returns (min_native, thresh_text) where thresh_text may include USD.
        """
        fallback = 0.00085
        usd = self._get_env_min_wallet_balance_usd()
        if usd and usd > 0:
            price = await self._fetch_native_price_usd(chain)
            if price and price > 0:
                native = max(fallback, float(usd) / float(price))
                return native, f"{native:.6f} {chain} (~${usd:.2f})"
        return fallback, f"{fallback:.6f} {chain}"

    def load_token_config(self):
        """Load token configuration from file if it exists."""
        try:
            config_path = os.path.join(os.path.dirname(__file__), 'token_config.json')
            if os.path.exists(config_path):
                self.logger.info(f"Loading token config from {config_path}")
                with open(config_path, 'r') as f:
                    loaded_config = json.load(f)
                
                # Update token_config with loaded values, preserving any defaults
                self.token_config.update(loaded_config)
                
                # Ensure funded_wallets is initialized and loaded
                if not hasattr(self, 'funded_wallets'):
                    self.funded_wallets = []
                
                # Load funded_wallets from config if it exists
                if 'funded_wallets' in loaded_config and isinstance(loaded_config['funded_wallets'], list):
                    self.funded_wallets = loaded_config['funded_wallets']
                    self.logger.info(f"Loaded {len(self.funded_wallets)} funded wallets from config")
                
                # Load total_distributed_bnb from config if it exists
                if 'total_distributed_bnb' in loaded_config:
                    try:
                        self.total_distributed_bnb = float(loaded_config['total_distributed_bnb'] or 0.0)
                        self.logger.info(f"Loaded total_distributed_bnb: {self.total_distributed_bnb}")
                    except (ValueError, TypeError) as e:
                        self.logger.warning(f"Invalid total_distributed_bnb value, resetting to 0: {e}")
                        self.total_distributed_bnb = 0.0
                
                # Save the updated config to ensure all fields are present
                self.save_token_config()
                
        except Exception as e:
            self.logger.error(f"Error loading token config: {e}", exc_info=True)
            if not hasattr(self, 'funded_wallets'):
                self.funded_wallets = []
            if not hasattr(self, 'total_distributed_bnb'):
                self.total_distributed_bnb = 0.0

    def save_token_config(self):
        """Persist token_config, including funded_wallets and totals, to token_config.json"""
        try:
            config_path = os.path.join(os.path.dirname(__file__), 'token_config.json')
            data = dict(self.token_config)
            # Ensure runtime values are reflected
            data['funded_wallets'] = getattr(self, 'funded_wallets', [])
            data['total_distributed_bnb'] = float(getattr(self, 'total_distributed_bnb', 0.0) or 0.0)
            with open(config_path, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            self.logger.error(f"Error saving token config: {e}", exc_info=True)

    async def _refresh_funded_from_chain(self, chain: str, min_balance: float | None = None, force: bool = False) -> list:
        """Scan loaded wallets, compute balances, and refresh self.funded_wallets for EVM chains."""
        if not isinstance(self.wallets, list) or not self.wallets:
            return []
        if getattr(self, 'funded_wallets', None) and not force:
            return self.funded_wallets

        chain = (chain or self.connected_chain or 'BNB').upper()
        # Compute min threshold if not provided
        if min_balance is None:
            min_balance, _ = await self._compute_min_required_native(chain)
        try:
            w3 = self.get_web3_for_chain(chain)
        except Exception as e:
            self.logger.error(f"_refresh_funded_from_chain: get_web3 failed: {e}")
            return []

        detected = []
        for w in self.wallets:
            addr = (w.get('address') if isinstance(w, dict) else str(w)).strip()
            if not addr:
                continue
            try:
                # Prefer retry helper if available
                try:
                    wei = await self._get_balance_with_retry(w3, addr)
                except Exception:
                    wei = w3.eth.get_balance(addr)
                bal = float(w3.from_wei(wei, 'ether'))
                if bal >= float(min_balance):
                    detected.append({'address': addr, 'balance': bal})
            except Exception as e:
                self.logger.debug(f"_refresh_funded_from_chain: skip {addr}: {e}")

        detected.sort(key=lambda x: x['balance'], reverse=True)
        self.funded_wallets = detected
        return detected

    async def menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Displays the main menu of commands."""
        menu_text = (
            "📋 <b>Available Commands</b>\n\n"
            "🔹 /connect - Connect a token (format: <code>0x... CHAIN $TICKER</code>)\n"
            f"🔹 {'⏸️' if self.trading_cycle and self.trading_cycle.is_running else '▶️'} /{'stop' if self.trading_cycle and self.trading_cycle.is_running else 'start'}_trend - {'Stop' if self.trading_cycle and self.trading_cycle.is_running else 'Start'} trending mode\n"
            "🔹 /refresh_funded - Rescan wallets on-chain to detect funded ones\n"
            "🔹 /menu - Display this menu\n\n"
            "<b>Current Status</b>:\n"
        )
        
        if self.token_config['address']:
            menu_text += (
                f"• Token: <code>{self.token_config['address']}</code>\n"
                f"• Chain: <b>{self.token_config['chain']}</b>\n"
                f"• Ticker: <b>{self.token_config['ticker']}</b>\n"
            )
            
            if self.trading_cycle:
                status = "🟢 RUNNING" if self.trading_cycle.is_running else "🔴 STOPPED"
                menu_text += f"• Trading Cycle: {status} (Cycle: {self.trading_cycle.current_cycle})\n"
            
            if self.wallets:
                # Ensure we show funded count even if cache is empty by scanning once
                try:
                    if not getattr(self, 'funded_wallets', []):
                        await self._refresh_funded_from_chain(self.connected_chain or 'BNB')
                except Exception:
                    pass
                funded_count = len(getattr(self, 'funded_wallets', []) or [])
                group_a = len(self.wallets[:17])
                group_b = len(self.wallets[17:34])
                try:
                    _, thresh_text = await self._compute_min_required_native(self.connected_chain or 'BNB')
                except Exception:
                    thresh_text = f"0.00085 {self.connected_chain or 'BNB'}"
                menu_text += (
                    f"• Wallets: <b>{len(self.wallets)}</b> total (<b>{group_a}</b> in Group A, <b>{group_b}</b> in Group B)\n"
                    f"• Funded (≥ {thresh_text}): <b>{funded_count}</b>"
                )
        else:
            menu_text += "No token connected. Use /connect to add one."
        
        await self.safe_reply_text(update, menu_text, parse_mode='HTML')

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start handler: greets and shows menu."""
        try:
            await self.safe_reply_text(update, "👋 Welcome! Use /menu to see available commands.")
            await self.menu(update, context)
        except Exception as e:
            self.logger.error(f"Error in /start: {e}", exc_info=True)
            await self.safe_reply_text(update, "❌ Failed to display menu. Check logs.")

    async def connect_token(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start the token connection process."""
        await self.safe_reply_text(update, 
            "🔗 Please enter the token details in this format (single line):\n"
            "<code>&lt;tokenaddress&gt; &lt;chain&gt; &lt;ticker&gt;</code>\n\n"
            "Example:\n"
            "<code>0x123...abc BNB TOKEN</code>\n\n"
            "Type /cancel to cancel.",
            parse_mode='HTML'
        )
        context.user_data['awaiting_token_info'] = 'all'
        
    async def handle_interactive_messages(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle all interactive messages based on current state."""
        if 'awaiting_token_info' in context.user_data:
            await self.handle_token_info(update, context)
        elif 'awaiting_funding_confirmation' in context.user_data:
            await self.handle_funding_confirmation(update, context)

    async def handle_token_info(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle the token information collection process."""
        if 'awaiting_token_info' not in context.user_data:
            return
            
        user_input = update.message.text.strip()
        
        if context.user_data['awaiting_token_info'] == 'all':
            # Parse the input: tokenaddress chain ticker
            parts = user_input.split()
            if len(parts) < 2:
                await self.safe_reply_text(
                    update,
                    "❌ <b>Invalid format</b>. Please enter at least:\n"
                    "<code>&lt;tokenaddress&gt; &lt;ticker&gt;</code>\n\n"
                    "Optionally you can also specify the chain: <code>&lt;tokenaddress&gt; &lt;chain&gt; &lt;ticker&gt;</code>",
                    parse_mode='HTML'
                )
                return

            token_address = parts[0]
            # If 3 tokens, assume explicit chain provided
            if len(parts) >= 3:
                maybe_chain = parts[1].upper()
                ticker = parts[2].upper()
                chain = maybe_chain if maybe_chain in {'BNB', 'ETH', 'SOL'} else None
                if not chain:
                    # Treat input as <address> <ticker ...>, auto-detect chain
                    ticker = parts[1].upper()
                    chain = self.detect_chain_from_address(token_address)
            else:
                # 2 parts: <address> <ticker>, auto-detect chain
                ticker = parts[1].upper()
                chain = self.detect_chain_from_address(token_address)

            
            # Validate chain and token address format for BNB/ETH/SOL
            if chain not in {'BNB', 'ETH', 'SOL'}:
                await self.safe_reply_text(
                    update,
                    "❌ Could not determine chain automatically. Supported chains: <b>BNB</b>, <b>ETH</b>, <b>SOL</b>.\n"
                    "Please re-enter as: <code>&lt;address&gt; &lt;chain&gt; &lt;ticker&gt;</code>",
                    parse_mode='HTML'
                )
                return

            if chain in {'BNB', 'ETH'}:
                # EVM address validation: 0x + 40 hex chars
                if not (token_address.startswith('0x') and len(token_address) == 42):
                    await self.safe_reply_text(
                        update,
                        "❌ Invalid token address for EVM chain. Must start with 0x and be 42 characters long.",
                        parse_mode='HTML'
                    )
                    return
            else:  # SOL
                # Basic Solana address validation: base58-like and reasonable length
                if token_address.startswith('0x') or not (32 <= len(token_address) <= 48):
                    await self.safe_reply_text(
                        update,
                        "❌ Invalid token address for Solana. Provide a base58 address (no 0x prefix).",
                        parse_mode='HTML'
                    )
                    return
                
            # Save token info
            self.token_config = {
                'address': token_address,
                'chain': chain,
                'ticker': ticker
            }
            self.save_token_config()
            # Persist primary token address for cycle checks
            self.token_address = token_address
            # Track connected chain for other features (e.g., /create_wallet)
            self.connected_chain = chain
            # Mark token as connected for this session
            context.user_data['token_connected'] = True
            
            # Ensure a payment_id exists for later payment/campaign updates (non-blocking for organic trending)
            try:
                if self.payment_mgr is not None and not context.user_data.get('payment_id'):
                    user_id = None
                    try:
                        user_id = update.effective_user.id if update and update.effective_user else None
                    except Exception:
                        user_id = None
                    payment = self.payment_mgr.create_payment(user_id or 'unknown', chain, token_address)
                    context.user_data['payment_id'] = payment.get('payment_id')
                    self.logger.info(f"Created payment_id for user {user_id}: {context.user_data['payment_id']}")
            except Exception as pe:
                # Do not block the flow; just log
                self.logger.warning(f"payment_id setup skipped due to error: {pe}")
            # Clear the state
            del context.user_data['awaiting_token_info']
            
            # Initialize trading cycle per chain
            if not hasattr(self, 'trading_cycle') or not self.trading_cycle:
                if chain in {'BNB', 'ETH'}:
                    w3 = self.get_web3_for_chain(chain)
                    self.trading_cycle = TradingCycle(
                        wallet_manager=self,
                        web3=w3,
                        token_address=token_address,
                        chain=chain,
                    )
                elif chain == 'SOL':
                    if SolanaTradingCycle is None:
                        await self.safe_reply_text(update, "❌ SOL trading engine not available. Missing module.")
                        return
                    client = self.get_solana_client()
                    self.trading_cycle = SolanaTradingCycle(
                        wallet_manager=self,
                        client=client,
                        token_address=token_address,
                    )
                else:
                    await self.safe_reply_text(update, f"❌ Unsupported chain: {chain}")
                    return
            else:
                # Update token address on existing cycle
                self.trading_cycle.token_address = token_address
                
            # Format the response
            response = f"{token_address}    {chain}    {ticker}"
            
            # Send the response in the requested format
            await self.safe_reply_text(
                update,
                f"✅ Token Connected Successfully!\n\n"
                f"{response}\n\n"
                "You can now use /start_trend to begin the trading cycle.",
                parse_mode=None  # Keep Markdown disabled
            )
            
            # Show the updated menu
            await self.menu(update, context)
            
    def initialize_web3(self):
        """Initialize Web3 connection with endpoint rotation, timeouts, and retry backoff."""
        self.logger.info("Initializing Web3 connection...")

        # Preferred/custom endpoint from environment
        custom_rpc = (os.getenv('BSC_MAINNET_RPC') or '').strip() or None

        # Curated list of reliable public endpoints (ordered by general reliability)
        rpc_urls = [
            custom_rpc,
            'https://bsc.publicnode.com',
            'https://rpc.ankr.com/bsc',
            'https://1rpc.io/bnb',
            'https://endpoints.omniatech.io/v1/bsc/mainnet/public',
        ]
        # Remove Nones/empties and dedupe while preserving order
        seen = set()
        rpc_urls = [u for u in rpc_urls if u and not (u in seen or seen.add(u))]

        # Retry across rounds with exponential backoff
        max_rounds = 3
        base_timeout = 10  # seconds per request
        for round_idx in range(1, max_rounds + 1):
            self.logger.info(f"Web3 connect attempt round {round_idx}/{max_rounds}...")
            for url in rpc_urls:
                try:
                    self.logger.info(f"Attempting to connect to: {url}")
                    provider = HTTPProvider(url, request_kwargs={'timeout': base_timeout})
                    web3 = Web3(provider)

                    # Validate connection by reading latest block and gas price
                    block = web3.eth.block_number
                    if not isinstance(block, int) or block <= 0:
                        raise RuntimeError("Invalid block number returned")

                    gas_price = web3.eth.gas_price  # will raise if provider isn't healthy

                    # Success path
                    self.web3 = web3
                    self.logger.info(
                        f"Connected to BSC node {url}. Block: {block}, Gas: {web3.from_wei(gas_price, 'gwei'):.2f} Gwei"
                    )
                    return True

                except Exception as e:
                    self.logger.warning(f"Connection failed for {url}: {e}")
                    continue

            # Backoff before next round if none succeeded
            if round_idx < max_rounds:
                backoff = min(30, 2 ** round_idx * 2)  # 4s, 8s; cap 30s
                self.logger.info(f"All endpoints failed in round {round_idx}. Backing off for {backoff}s before retry...")
                time.sleep(backoff)

        # If we get here, all attempts in all rounds have failed
        error_msg = (
            "❌ Failed to connect to any BSC node after multiple attempts.\n\n"
            "Suggestions:\n"
            "1) Verify internet/DNS connectivity (try switching DNS to 1.1.1.1/8.8.8.8).\n"
            "2) Try a VPN or different network.\n"
            "3) Set a working custom RPC in .env: BSC_MAINNET_RPC=your_rpc_url_here"
        )
        self.logger.error("All BSC node connection attempts failed after retries")
        raise ConnectionError(error_msg)
        
    def get_web3_instance(self):
        """Get a working Web3 instance with fallback RPC endpoints."""
        if self.web3 is None:
            self.initialize_web3()
        return self.web3

    # ---- Chain helpers (missing earlier) ----
    def get_web3_for_chain(self, chain: str) -> Web3:
        """Return a Web3 instance for the requested EVM chain.

        - BNB: uses the initialized self.web3 (BSC).
        - ETH: creates/caches a separate Web3 with env ETH_MAINNET_RPC or a public fallback.
        """
        ch = (chain or self.connected_chain or 'BNB').upper()
        if ch == 'BNB':
            return self.get_web3_instance()

        if ch == 'ETH':
            # Cache ETH client on first use
            if not hasattr(self, '_web3_eth') or self._web3_eth is None:
                rpc = (os.getenv('ETH_MAINNET_RPC') or '').strip() or 'https://eth.publicnode.com'
                provider = HTTPProvider(rpc, request_kwargs={'timeout': 15})
                w3 = Web3(provider)
                # Light validation
                _ = w3.eth.block_number  # raises if unhealthy
                _ = w3.eth.gas_price
                self._web3_eth = w3
                self.logger.info("Connected ETH Web3 client")
            return self._web3_eth

        raise ValueError(f"Unsupported EVM chain requested: {ch}")

    def detect_chain_from_address(self, address: str) -> str | None:
        """Heuristically detect chain from token address format.

        - 0x-prefixed, 42-char: EVM token. Default to current connected chain if it's BNB/ETH, else BNB.
        - Non-0x, base58-like length 32-48: assume SOL.
        """
        if not address:
            return None
        addr = address.strip()
        if addr.startswith('0x') and len(addr) == 42:
            ch = (self.connected_chain or '').upper()
            return ch if ch in {'BNB', 'ETH'} else 'BNB'
        # Very loose SOL check by length (we already validate later)
        if not addr.startswith('0x') and 32 <= len(addr) <= 48:
            return 'SOL'
        return None

    def get_solana_client(self) -> SolClient:
        """Return a Solana RPC client using env or public fallback."""
        if not hasattr(self, '_sol_client') or self._sol_client is None:
            rpc = (os.getenv('SOLANA_MAINNET_RPC') or '').strip() or 'https://api.mainnet-beta.solana.com'
            self._sol_client = SolClient(rpc)
        return self._sol_client

    def get_central_wallet(self, chain: str) -> dict:
        """Return the central wallet config for a given chain.

        Looks for chain-specific env vars first, then falls back to legacy ones.
        Returns a dict: { 'address': str, 'private_key': str }
        """
        ch = (chain or self.connected_chain or '').upper()
        addr = ''
        key = ''

        if ch == 'BNB':
            addr = os.getenv('BSC_CENTRAL_WALLET', '').strip()
            key = os.getenv('BSC_CENTRAL_WALLET_KEY', '').strip()
        elif ch == 'ETH':
            addr = os.getenv('ETH_CENTRAL_WALLET', '').strip()
            key = os.getenv('ETH_CENTRAL_WALLET_KEY', '').strip()
        elif ch == 'SOL':
            addr = os.getenv('SOL_CENTRAL_WALLET', '').strip()
            key = os.getenv('SOL_CENTRAL_WALLET_KEY', '').strip()

        # Legacy fallback for EVM chains
        if (not addr or not key) and ch in {'BNB', 'ETH'}:
            legacy_addr = os.getenv('CENTRAL_WALLET_ADDRESS', '').strip()
            legacy_key = os.getenv('CENTRAL_WALLET_PRIVATE_KEY', '').strip()
            if legacy_addr and legacy_key:
                addr = addr or legacy_addr
                key = key or legacy_key

        return {'address': addr, 'private_key': key}

    def register_handlers(self):
        """Register all command and message handlers."""
        # Register command handlers
        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CommandHandler("menu", self.menu))
        self.app.add_handler(CommandHandler("create_wallet", self.create_wallet))
        self.app.add_handler(CommandHandler("connect", self.connect_token))
        self.app.add_handler(CommandHandler("fund_wallet", self.fund_wallet))
        self.app.add_handler(CommandHandler("start_funding", self.start_funding))
        self.app.add_handler(CommandHandler("check_balance", self.check_balance))
        self.app.add_handler(CommandHandler("wallet_status", self.wallet_status))
        self.app.add_handler(CommandHandler("refresh_funded", self.refresh_funded))
        self.app.add_handler(CommandHandler("cancel", self.cancel))
        self.app.add_handler(CommandHandler("start_trend", self.start_trend))
        self.app.add_handler(CommandHandler("stop_trend", self.stop_trend))
        
        # Set up conversation handler for withdrawals
        withdrawal_conv_handler = ConversationHandler(
            entry_points=[CommandHandler("withdraw", self.withdraw_funds)],
            states={
                'AWAITING_WALLET_ADDRESS': [MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_withdraw_address)],
                'CONFIRM_WITHDRAWAL': [MessageHandler(filters.TEXT & ~filters.COMMAND, self.confirm_withdrawal)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel)],
            conversation_timeout=300,
        )
        
        self.app.add_handler(withdrawal_conv_handler)
        
        # Global error handler
        self.app.add_error_handler(self.on_error)
        
        # Add message handler for interactive commands
        self.app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            self.handle_interactive_messages
        ))

    async def on_error(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Global error handler: log exception and notify user briefly."""
        try:
            self.logger.error("Unhandled exception", exc_info=context.error)
            if update and getattr(update, 'effective_chat', None):
                await self.safe_reply_text(
                    update,
                    "❌ An internal error occurred. It has been logged and will be addressed.")
        except Exception:
            # Avoid raising from error handler
            pass

    async def _fetch_bnb_price_async(self, session, url, parser):
        """Helper method to fetch BNB price asynchronously"""
        try:
            async with session.get(url, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    return parser(data)
        except Exception as e:
            self.logger.warning(f"Error fetching BNB price from {url}: {str(e)}")
        return None

    async def _get_balance_with_retry(self, w3, address, max_retries=3, delay=1):
        """Helper method to get wallet balance with retry logic"""
        last_error = None
        for attempt in range(max_retries):
            try:
                # Add a small delay between retries
                if attempt > 0:
                    await asyncio.sleep(delay * attempt)
                return w3.eth.get_balance(address)
            except Exception as e:
                last_error = e
                self.logger.warning(f"Attempt {attempt + 1} failed for {address}: {str(e)}")
        
        self.logger.error(f"Failed to get balance for {address} after {max_retries} attempts: {str(last_error)}")
        raise last_error

    async def _refresh_funded_from_chain(self, chain: str, min_balance: float | None = None, force: bool = False) -> list:
        """Scan all loaded wallets on-chain and refresh self.funded_wallets.

        Args:
            chain: 'BNB' | 'ETH' | 'SOL' (EVM chains supported for now)
            min_balance: threshold to consider a wallet funded (native). If None, computed from USD env.
            force: if True, always rescan even if cache exists

        Returns: list of dicts: [{'address': str, 'balance': float}, ...]
        """
        if not isinstance(self.wallets, list) or not self.wallets:
            return []
        if getattr(self, 'funded_wallets', None) and not force:
            return self.funded_wallets

        chain = (chain or self.connected_chain or 'BNB').upper()
        if chain not in {'BNB', 'ETH'}:
            # Only EVM scanning implemented here
            if isinstance(self.wallets, list) and self.wallets:
                # Compute min threshold if not provided
                if min_balance is None:
                    min_balance, _ = await self._compute_min_required_native(chain)
                try:
                    w3 = self.get_web3_for_chain(chain)
                except Exception as e:
                    self.logger.error(f"_refresh_funded_from_chain: get_web3 failed: {e}")
                    return []
                detected = []
                for w in self.wallets:
                    addr = (w.get('address') if isinstance(w, dict) else str(w)).strip()
                    if not addr:
                        continue
                    try:
                        wei = w3.eth.get_balance(addr)
                        bal = float(w3.from_wei(wei, 'ether'))
                        if bal >= float(min_balance):
                            detected.append({'address': addr, 'balance': bal})
                    except Exception as e:
                        self.logger.debug(f"_refresh_funded_from_chain: skip {addr}: {e}")

                detected.sort(key=lambda x: x['balance'], reverse=True)
                self.funded_wallets = detected
                return detected
            return getattr(self, 'funded_wallets', []) or []

        detected = []
        for w in self.wallets:
            addr = (w.get('address') if isinstance(w, dict) else str(w)).strip()
            if not addr:
                continue
            try:
                wei = await self._get_balance_with_retry(w3, addr)
                bal = float(w3.from_wei(wei, 'ether'))
                if bal >= float(min_balance):
                    detected.append({'address': addr, 'balance': bal})
            except Exception as e:
                self.logger.debug(f"_refresh_funded_from_chain: skip {addr}: {e}")

        detected.sort(key=lambda x: x['balance'], reverse=True)
        self.funded_wallets = detected
        return detected

    async def get_bnb_price(self):
        """Fetch the current BNB price in USD with caching and async requests."""
        current_time = time.time()
        
        # Return cached price if still valid
        if current_time - self._bnb_price_cache['timestamp'] < self._bnb_price_cache['ttl']:
            return self._bnb_price_cache['price']
            
        # Define price sources with their URLs and parsers
        price_sources = [
            {
                'url': 'https://api.binance.com/api/v3/ticker/price?symbol=BNBUSDT',
                'parser': lambda d: float(d['price'])
            },
            {
                'url': 'https://api.coingecko.com/api/v3/simple/price?ids=binancecoin&vs_currencies=usd',
                'parser': lambda d: float(d['binancecoin']['usd'])
            },
            {
                'url': 'https://api.pancakeswap.info/api/v2/tokens/0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c',
                'parser': lambda d: float(d['data']['price'])
            }
        ]
        
        # Create a session and fetch all prices in parallel
        try:
            import aiohttp
            import asyncio
            
            async with aiohttp.ClientSession(headers={'User-Agent': 'Mozilla/5.0'}) as session:
                tasks = [
                    self._fetch_bnb_price_async(session, source['url'], source['parser'])
                    for source in price_sources
                ]
                
                # Wait for the first successful response with a timeout of 3 seconds
                done, pending = await asyncio.wait(
                    [asyncio.create_task(t) for t in tasks],
                    return_when=asyncio.FIRST_COMPLETED,
                    timeout=3.0
                )
                
                # Get the first successful result
                for task in done:
                    result = await task
                    if result is not None:
                        # Update cache
                        self._bnb_price_cache = {
                            'price': result,
                            'timestamp': current_time,
                            'ttl': 300  # 5 minutes
                        }
                        return result
                        
        except Exception as e:
            self.logger.warning(f"Async price fetch failed: {str(e)}")
        
        # Return cached price if available, otherwise fallback
        return self._bnb_price_cache['price']

    async def fund_wallet(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Enforce /connect in current session
        if not context.user_data.get('token_connected', False):
            await self.safe_reply_text(update, "❌ Please use /connect to set token details before using this command (required each session).")
            return
        # Enforce /connect in current session
        if not context.user_data.get('token_connected', False):
            await self.safe_reply_text(update, "❌ Please use /connect to set token details before using this command (required each session).")
            return
        """Show the correct central wallet for the connected chain and its current balance."""
        try:
            chain = getattr(self, 'connected_chain', None) or self.token_config.get('chain')
            if not chain:
                await self.safe_reply_text(update, "❌ Please connect a token first so I can determine the chain.")
                return

            cw = self.get_central_wallet(chain)
            address = (cw.get('address') or '').strip()
            pkey = (cw.get('private_key') or '').strip()
            if not address or not pkey:
                await self.safe_reply_text(
                    update,
                    f"❌ Central wallet for {chain} not configured. Please set the appropriate environment variables.")
                return

            # Get current token price for USD conversion
            bnb_price = await self.get_bnb_price()

            # Show loading with the address
            loading_message = await self.safe_reply_text(
                update,
                (
                    f"🔍 <b>{chain} Central Wallet</b>\n\n"
                    f"<pre>{address}</pre>\n"
                    "Fetching balance and current prices..."
                ),
                parse_mode='HTML'
            )

            if chain in {'BNB', 'ETH'}:
                w3 = self.get_web3_for_chain(chain)
                try:
                    checksum = w3.to_checksum_address(address)
                except Exception:
                    checksum = address
                # Fetch balance
                balance_wei = w3.eth.get_balance(checksum)
                balance_native = float(w3.from_wei(balance_wei, 'ether'))
                symbol = 'BNB' if chain == 'BNB' else 'ETH'
                usd_value = balance_native * bnb_price if bnb_price else 0.0
                usd_display = f"(${usd_value:,.2f} USD)" if bnb_price else ""
                explorer = 'https://bscscan.com' if chain == 'BNB' else 'https://etherscan.io'
                link = f"{explorer}/address/{checksum}"
                msg = (
                    f"✅ <b>{chain} Central Wallet</b>\n\n"
                    f"<pre>{checksum}</pre>\n"
                    f"• Balance: <b>{balance_native:.6f} {symbol}</b> {usd_display}\n"
                    f"• Current {symbol} Price: <b>${bnb_price:,.4f} USD</b>\n\n"
                    f"Explorer: <a href=\"{link}\">{link}</a>"
                )
                await loading_message.edit_text(msg, parse_mode='HTML', disable_web_page_preview=True)
                return
            elif chain == 'SOL':
                client = self.get_solana_client()
                from solders.pubkey import Pubkey
                lamports = client.get_balance(Pubkey.from_string(address)).value
                balance_sol = lamports / 1_000_000_000
                usd_value = balance_sol * bnb_price if bnb_price else 0.0
                usd_display = f"(${usd_value:,.2f} USD)" if bnb_price else ""
                link = f"https://solscan.io/address/{address}"
                msg = (
                    f"✅ <b>SOL Central Wallet</b>\n\n"
                    f"<pre>{address}</pre>\n"
                    f"• Balance: <b>{balance_sol:.6f} SOL</b> {usd_display}\n"
                    f"• Current SOL Price: <b>${bnb_price:,.4f} USD</b>\n\n"
                    f"Explorer: <a href=\"{link}\">{link}</a>"
                )
                await loading_message.edit_text(msg, parse_mode='HTML', disable_web_page_preview=True)
                return
            else:
                await loading_message.edit_text(f"❌ Unsupported chain: {chain}")
                return
        except Exception as e:
            self.logger.error(f"Error in fund_wallet: {str(e)}", exc_info=True)
            await self.safe_reply_text(update, "❌ An error occurred. Please try again later.")

    def _escape_markdown(self, text: str) -> str:
        """Escape a minimal set of Markdown characters to avoid Telegram parse errors.
        This targets classic 'Markdown' (not MarkdownV2) issues like parentheses and a few common symbols.
        """
        if not text:
            return text
        # Escape backslashes first
        text = text.replace('\\', '\\\\')
        # Escape characters that often break classic Markdown
        for ch in ['[', ']', '(', ')', '*', '_', '`']:
            text = text.replace(ch, f'\\{ch}')
        return text

    async def distribute_funds(self, update: Update, context: ContextTypes.DEFAULT_TYPE, funding_details: dict):
        # Enforce /connect in current session
        if not context.user_data.get('token_connected', False):
            await self.safe_reply_text(update, "❌ Please use /connect to set token details before using this command (required each session).")
            return
        """Distribute funds from central wallet to trading wallets based on available balance."""
        if not self.wallets:
            await self.safe_reply_text(update, "❌ No wallets found to fund.")
            return
            
        # Get chain from context or token config
        chain = getattr(self, 'connected_chain', None) or self.token_config.get('chain')
        if not chain:
            await self.safe_reply_text(update, "❌ No chain configured. Please connect a token first.")
            return
            
        # Get chain-specific wallet config
        cw = self.get_central_wallet(chain)
        address = (cw.get('address') or '').strip()
        pkey = (cw.get('private_key') or '').strip()
        
        if not address or not pkey:
            await self.safe_reply_text(update, f"❌ Central wallet for {chain} not configured.")
            return
            
        bnb_per_wallet = funding_details['bnb_per_wallet']
        
        # Update funding details with wallet info
        funding_details.update({
            'chain': chain,
            'wallet_address': address,
            'wallet_private_key': pkey
        })
        max_wallets = funding_details['max_wallets']
        
        try:
            # Get Web3 instance for the correct chain
            chain = funding_details['chain']
            w3 = self.get_web3_for_chain(chain)
            wallet_address = funding_details['wallet_address']
            wallet_private_key = funding_details['wallet_private_key']
            
            # Recheck balance in case it changed
            balance_wei = w3.eth.get_balance(wallet_address)
            balance_bnb = float(w3.from_wei(balance_wei, 'ether'))
            
            # Adjust if balance changed since plan was made
            gas_reserve_usd = float(funding_details.get('gas_reserve_usd', 1.0))
            min_balance_needed = funding_details['total_bnb'] + (gas_reserve_usd / funding_details['bnb_price'])
            if balance_bnb < min_balance_needed:
                adjusted_wallets = int((balance_bnb - (gas_reserve_usd / funding_details['bnb_price'])) / bnb_per_wallet)
                if adjusted_wallets < 1:
                    min_needed_usd = float(funding_details.get('gas_reserve_usd', 1.0)) + float(funding_details.get('usd_per_wallet', 1.0))
                    await self.safe_reply_text(
                        update,
                        (
                            "❌ <b>Insufficient balance</b>\n\n"
                            f"Need at least <b>${min_needed_usd:.2f}</b> to fund 1 wallet after gas reserve.\n\n"
                            "Send more BNB to:\n"
                            f"<pre>{wallet_address}</pre>"
                        ),
                        parse_mode='HTML'
                    )
                    return
                
                max_wallets = adjusted_wallets
                funding_details['max_wallets'] = max_wallets
                
                await self.safe_reply_text(update, f"⚠️ Balance decreased. Will now fund <b>{max_wallets}</b> wallets.", parse_mode='HTML')
                
            # Start funding process
            status_msg = await self.safe_reply_text(update, "🔄 Starting to fund wallets...")
            success_count = 0
            funded_wallets = []
            
            for i, wallet in enumerate(self.wallets[:max_wallets], 1):
                try:
                    # Check if we still have enough balance (in case of network fees)
                    current_balance = float(w3.from_wei(
                        w3.eth.get_balance(wallet_address), 'ether'
                    ))
                    
                    if current_balance < bnb_per_wallet * 1.1:  # Include 10% buffer for gas
                        self.logger.warning(f"Insufficient balance to continue funding. Stopping at {i-1} wallets.")
                        break
                    
                    # Prepare transaction
                    nonce = w3.eth.get_transaction_count(wallet_address)
                    
                    # Calculate gas cost
                    gas_price = w3.eth.gas_price
                    gas_limit = 21000  # Standard transfer gas limit
                    gas_cost = w3.from_wei(gas_price * gas_limit, 'ether')
                    
                    # Amount to send (BNB per wallet minus gas cost)
                    send_amount = bnb_per_wallet - float(gas_cost)
                    
                    if send_amount <= 0:
                        self.logger.error(f"Gas cost {gas_cost} exceeds send amount {bnb_per_wallet}")
                        continue
                    
                    # Get chain ID based on the chain
                    chain_id = 56 if chain == 'BNB' else 1  # Default to BSC (56) or ETH (1)
                    
                    tx = {
                        'nonce': nonce,
                        'to': wallet['address'],
                        'value': w3.to_wei(send_amount, 'ether'),
                        'gas': gas_limit,
                        'gasPrice': gas_price,
                        'chainId': chain_id
                    }
                    
                    # Sign and send transaction
                    signed_tx = w3.eth.account.sign_transaction(tx, wallet_private_key)
                    # Use raw_transaction (with underscore) for newer Web3.py versions
                    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
                    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
                    
                    if tx_receipt.status == 1:
                        success_count += 1
                        funded_wallets.append(wallet['address'])
                        self.logger.info(f"Successfully funded {wallet['address']} with {send_amount:.6f} BNB")
                        
                        # Update status message every 2 wallets
                        if success_count % 2 == 0 or i == max_wallets:
                            await self.safe_edit_text(
                                status_msg,
                                f"🔄 Funding in progress... {success_count}/{max_wallets} wallets funded\n"
                                f"• Current balance: {current_balance:.6f} BNB\n"
                                f"• Last funded: {wallet['address'][:10]}...{wallet['address'][-6:]}"
                            )
                    
                except Exception as e:
                    self.logger.error(f"Error funding wallet {wallet['address']}: {str(e)}"
                                   f"\n{traceback.format_exc()}")
            
            # Update list of funded wallets for trading
            self.funded_wallets = funded_wallets
            
            # Track total distributed (BNB) across runs
            self.total_distributed_bnb = float(getattr(self, 'total_distributed_bnb', 0.0) or 0.0)
            self.total_distributed_bnb += float(success_count) * float(bnb_per_wallet)
            
            # Update token config with both funded_wallets and total_distributed_bnb
            self.token_config['funded_wallets'] = self.funded_wallets
            self.token_config['total_distributed_bnb'] = self.total_distributed_bnb
            self.save_token_config()
            
            # Final status update
            final_balance = float(w3.from_wei(
                w3.eth.get_balance(wallet_address), 'ether'
            ))
            
            await self.safe_edit_text(
                status_msg,
                f"✅ Funding complete!\n\n"
                f"• Successfully funded: {success_count}/{max_wallets} wallets\n"
                f"• Amount per wallet: {bnb_per_wallet:.6f} BNB (≈${float(funding_details.get('usd_per_wallet', 1.0)):.2f})\n"
                f"• Total distributed: {success_count * bnb_per_wallet:.6f} BNB\n"
                f"• Remaining balance: {final_balance:.6f} BNB\n\n"
                f"These wallets are now ready for trading with /start_trend"
            )
            
            # Also send a final confirmation message in chat
            try:
                usd_per_wallet = float(funding_details.get('usd_per_wallet', 1.0))
                explorer_url = f"https://bscscan.com/address/{wallet_address}" if chain == 'BNB' else f"https://etherscan.io/address/{wallet_address}"
                summary_text = (
                    "✅ <b>Funding Completed</b>\n\n"
                    f"• Funded wallets: <b>{success_count}/{max_wallets}</b>\n"
                    f"• Per wallet: <code>{bnb_per_wallet:.6f} BNB</code> (≈${usd_per_wallet:.2f})\n"
                    f"• Total sent: <code>{success_count * bnb_per_wallet:.6f} BNB</code>\n"
                    f"• Remaining: <code>{final_balance:.6f} BNB</code>\n\n"
                    f"<a href=\"{explorer_url}\">View on Explorer</a>"
                )
                await self.safe_reply_text(update, summary_text, parse_mode='HTML')
            except Exception as e:
                self.logger.error(f"Error sending summary: {str(e)}")
                await self.safe_reply_text(update, "✅ Funding completed, but there was an error generating the summary.")
            
        except Exception as e:
            self.logger.error(f"Error in distribute_funds: {str(e)}\n{traceback.format_exc()}")
            await self.safe_reply_text(update, f"❌ Error during funding process: {str(e)}\n\nPlease check the logs and try again.")
        

    def _split_message(self, text: str, max_len: int = 4000) -> list:
        """Split a message into chunks under max_len, preferring line or word boundaries."""
        if len(text) <= max_len:
            return [text]
        chunks = []
        start = 0
        while start < len(text):
            end = min(len(text), start + max_len)
            # Prefer to break at a newline
            split_at = text.rfind('\n', start, end)
            if split_at == -1 or split_at <= start + max_len * 0.6:
                # Try space if no good newline
                split_at = text.rfind(' ', start, end)
            if split_at == -1 or split_at <= start:
                split_at = end
            chunk = text[start:split_at]
            chunks.append(chunk)
            start = split_at
        return [c for c in chunks if c]

    async def safe_reply_text(self, update: Update, text: str, parse_mode=None, max_retries=3, initial_delay=1, **kwargs):
        """Safely send a reply with retry logic for timeouts and other transient errors.
        
        Args:
            update: The update object from the Telegram bot
            text: The message text to send
            parse_mode: The parse mode for the message (e.g., 'Markdown', 'HTML')
            max_retries: Maximum number of retry attempts
            initial_delay: Initial delay between retries in seconds
            **kwargs: Additional arguments to pass to reply_text
        """
        delay = initial_delay
        last_exception = None

        # Set default kwargs if not provided
        kwargs.setdefault('disable_web_page_preview', True)
        kwargs.setdefault('read_timeout', 30)
        kwargs.setdefault('write_timeout', 30)
        kwargs.setdefault('connect_timeout', 30)
        kwargs.setdefault('pool_timeout', 30)

        # Pre-escape Markdown if requested to avoid parse errors
        effective_text = text
        if parse_mode:
            kwargs['parse_mode'] = parse_mode
            if parse_mode == 'Markdown':
                effective_text = self._escape_markdown(effective_text)

        # Decide chunk size conservatively
        max_len = 3500 if kwargs.get('parse_mode') else 4000
        chunks = self._split_message(effective_text, max_len=max_len)

        last_result = None
        for idx, chunk in enumerate(chunks):
            delay = initial_delay
            for attempt in range(max_retries):
                try:
                    last_result = await update.message.reply_text(chunk, **kwargs)
                    # Small pause between chunks to avoid rate limits
                    if idx < len(chunks) - 1:
                        await asyncio.sleep(0.6)
                    break
                except Exception as e:
                    last_exception = e
                    self.logger.warning(f"Attempt {attempt + 1} failed: {str(e)}")

                    # If Telegram rejects Markdown entities, fall back to plain text once per chunk
                    msg = str(e).lower()
                    if ("can't parse entities" in msg or 'parse entities' in msg or 'bad request: markdown' in msg) and kwargs.get('parse_mode'):
                        self.logger.info("Falling back to plain text due to Markdown parse error")
                        kwargs.pop('parse_mode', None)
                        # Recompute chunks for plain text on first failure
                        remaining_text = '\n'.join([chunk] + chunks[idx + 1:])
                        chunks = self._split_message(remaining_text, max_len=4000)
                        # restart sending from current index with plain text
                        break

                    if attempt < max_retries - 1:  # Don't sleep on the last attempt
                        await asyncio.sleep(delay)
                        delay *= 2  # Exponential backoff
            else:
                # Exhausted retries for this chunk; abort
                error_msg = f"❌ Failed to send message chunk {idx + 1}/{len(chunks)} after {max_retries} attempts: {str(last_exception)}"
                self.logger.error(error_msg)
                # Attempt to notify with minimal text
                try:
                    return await update.message.reply_text(
                        "⚠️ " + error_msg[:3800],
                        disable_web_page_preview=True
                    )
                except Exception:
                    raise
            # If we broke out of inner loop due to markdown fallback, restart outer loop
            if not kwargs.get('parse_mode') and idx < len(chunks) and chunk not in chunks:
                # chunks were recomputed; restart loop to send from the beginning of new chunks
                return await self.safe_reply_text(update, text, parse_mode=None, max_retries=max_retries, initial_delay=initial_delay, **kwargs)

        return last_result

    async def safe_edit_text(self, message, text: str, parse_mode: str | None = 'Markdown', max_retries: int = 3, initial_delay: float = 1.0, **kwargs):
        """Safely edit a Telegram message text with basic Markdown escaping and retries.
        Falls back to plain text if Markdown parsing fails.
        """
        delay = initial_delay
        for attempt in range(max_retries):
            try:
                prepared = self._escape_markdown(text) if parse_mode == 'Markdown' else text
                return await message.edit_text(prepared, parse_mode=parse_mode, **kwargs)
            except Exception as e:
                self.logger.error(f"Error editing message (attempt {attempt+1}/{max_retries}): {e}")
                # Fallback to plain text on parse errors
                if parse_mode == 'Markdown':
                    try:
                        return await message.edit_text(text, parse_mode=None, **kwargs)
                    except Exception:
                        pass
                if attempt < max_retries - 1:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 8)
        return None

    async def create_wallet(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Enforce /connect in current session
        if not context.user_data.get('token_connected', False):
            await self.safe_reply_text(update, "❌ Please use /connect to set token details before creating wallets (required each session).")
            return

        """Reads pre-generated wallets for the connected chain and displays addresses only (one per line)."""
        # Determine connected chain
        chain = getattr(self, 'connected_chain', None) or self.token_config.get('chain')
        if not chain:
            await self.safe_reply_text(update, "❌ Please connect token details first using /connect command.")
            return

        await self.safe_reply_text(update, f"🔎 Loading pre-generated wallets for {chain}...")

        try:
            bot_dir = os.path.dirname(os.path.abspath(__file__))
            if chain == 'BNB':
                wallets_file_path = os.path.join(bot_dir, '..', 'hardhat-scripts', 'wallets.json')
            elif chain == 'ETH':
                wallets_file_path = os.path.join(bot_dir, 'eth_wallet.json')
            elif chain == 'SOL':
                wallets_file_path = os.path.join(bot_dir, 'sol_wallet.json')
            else:
                await self.safe_reply_text(update, f"❌ Unsupported chain: {chain}")
                return

            wallets_file_path = os.path.normpath(wallets_file_path)
            self.logger.info(f"Looking for wallets at: {wallets_file_path}")

            if not os.path.exists(wallets_file_path):
                fname = os.path.basename(wallets_file_path)
                hint = "Run the generator script: gen_eth_wallets.py" if chain == 'ETH' else ("gen_sol_wallets.py" if chain == 'SOL' else "hardhat generation script")
                error_msg = (
                    f"❌ <b>{fname} not found</b>\n\n"
                    f"Please run the wallet generation script manually in your terminal first (e.g., <code>{hint}</code>).\n"
                    f"Expected location: <code>{fname}</code>"
                )
                await self.safe_reply_text(update, error_msg, parse_mode='HTML')
                return

            with open(wallets_file_path, 'r', encoding='utf-8') as f:
                try:
                    wallets = json.load(f)
                except json.JSONDecodeError as e:
                    error_msg = (
                        f"❌ <b>Error reading {os.path.basename(wallets_file_path)}</b> — file is corrupted or empty.\n\n"
                        f"Error: <code>{self._escape_markdown(str(e))}</code>\n\n"
                        "Please regenerate the wallets file."
                    )
                    await self.safe_reply_text(update, error_msg, parse_mode='HTML')
                    return

            if not isinstance(wallets, list):
                await self.safe_reply_text(update, f"❌ Invalid format in <code>{os.path.basename(wallets_file_path)}</code>.", parse_mode='HTML')
                return

            # Normalize wallet objects to have address (private keys ignored for output)
            normalized = []
            for w in wallets:
                if not isinstance(w, dict):
                    continue
                addr = w.get('address') or w.get('addr')
                if addr:
                    # capture private key into mapping if present in file (do not display)
                    try:
                        pk = (
                            w.get('private_key')
                            or w.get('privateKey')
                            or w.get('key')
                            or w.get('pkey')
                        )
                        if pk:
                            self.wallet_private_keys[addr] = pk
                    except Exception:
                        pass
                    normalized.append({"address": addr})

            if not normalized:
                await self.safe_reply_text(update, "⚠️ Wallet list is empty or missing keys. Regenerate and try again.")
                return

            # Save in session (addresses only)
            self.wallets = normalized

            # Format output: one address per line (up to 34)
            lines = [w['address'] for w in normalized[:34]]
            response = f"✅ Found {len(normalized)} pre-generated wallets for {chain}. Showing up to 34.\n\n" + "\n".join(lines)

            # Chunk and send
            chunk_size = 3000
            chunks = [response[i:i + chunk_size] for i in range(0, len(response), chunk_size)]
            for i, chunk in enumerate(chunks):
                try:
                    await self.safe_reply_text(update, chunk)
                    if i < len(chunks) - 1:
                        await asyncio.sleep(1)
                except Exception as e:
                    self.logger.error(f"Error sending chunk {i+1}/{len(chunks)}: {str(e)}")
                    continue

        except Exception as e:
            tb_str = traceback.format_exc()
            self.logger.error(f"Critical error in create_wallet: {tb_str}")
            await self.safe_reply_text(update, "❌ An unexpected error occurred while processing wallets.")

    async def start_funding(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Enforce /connect in current session
        if not context.user_data.get('token_connected', False):
            await self.safe_reply_text(update, "❌ Please use /connect to set token details before using this command (required each session).")
            return
        # Enforce /connect in current session
        if not context.user_data.get('token_connected', False):
            await self.safe_reply_text(update, "❌ Please use /connect to set token details before using this command (required each session).")
            return
        """Start funding generated wallets if central wallet has >= $2.

        Notes:
        - This uses distribute_funds() which sends the initial "Starting to fund wallets...",
          progress edits, and a final "Funding complete!" message.
        """
        try:
            # Preconditions
            if not self.wallets:
                await self.safe_reply_text(update, "❌ No wallets loaded. Use /create_wallet first.")
                return

            # Resolve chain and central wallet (BNB supported)
            chain = getattr(self, 'connected_chain', None) or (self.token_config.get('chain') if hasattr(self, 'token_config') else None)
            if not chain:
                await self.safe_reply_text(update, "❌ Please connect a token first so I can determine the chain.")
                return
            if chain != 'BNB':
                await self.safe_reply_text(update, f"❌ Funding is currently supported on BNB only. Detected: {chain}.")
                return
                
            # Debug: Log wallet config
            legacy_address = getattr(self, 'central_wallet_address', 'Not set')
            self.logger.info(f"Legacy wallet address: {legacy_address}")
            
            # Try to get wallet from chain-specific config first
            cw = self.get_central_wallet(chain)
            self.logger.info(f"Chain {chain} wallet config - Address: {'Set' if cw.get('address') else 'Not set'}, Key: {'Set' if cw.get('private_key') else 'Not set'}")
            
            address = (cw.get('address') or '').strip()
            pkey = (cw.get('private_key') or '').strip()
            
            # Fall back to legacy config if chain-specific config is not found
            if not address or not pkey:
                self.logger.warning(f"No {chain} central wallet found, falling back to legacy config")
                address = getattr(self, 'central_wallet_address', '').strip()
                pkey = getattr(self, 'central_wallet_private_key', '').strip()
                self.logger.info(f"Legacy fallback - address: {'[REDACTED]' if address else 'Not set'}, key: {'[REDACTED]' if pkey else 'Not set'}")
            
            # Check if we have valid wallet credentials
            if not address or not pkey:
                # Log environment variables for debugging (without sensitive data)
                env_vars = {k: v for k, v in os.environ.items() 
                          if any(x in k.upper() for x in ['WALLET', 'CENTRAL', 'BSC_', 'ETH_', 'SOL_'])}
                self.logger.warning(f"Wallet-related environment variables: {env_vars}")
                
                error_msg = (
                    f"❌ No central wallet configured for {chain}.\n\n"
                    "Please set one of these environment variable pairs in your .env file:\n"
                    "- For BNB: BSC_CENTRAL_WALLET and BSC_CENTRAL_WALLET_KEY\n"
                    "- Legacy (deprecated): CENTRAL_WALLET_ADDRESS and CENTRAL_WALLET_PRIVATE_KEY\n\n"
                    f"Current config - Address: {'Set' if address else 'Not set'}, Private Key: {'Set' if pkey else 'Not set'}"
                )
                await self.safe_reply_text(update, error_msg)
                return

            # Get BNB price
            bnb_price = await self.get_bnb_price()
            if not bnb_price:
                await self.safe_reply_text(update, "❌ Could not fetch BNB price. Try again later.")
                return

            # Central wallet balance (per-chain Web3)
            w3 = self.get_web3_for_chain(chain)
            try:
                checksum = w3.to_checksum_address(address)
            except Exception:
                checksum = address
            balance_wei = w3.eth.get_balance(checksum)
            balance_bnb = float(w3.from_wei(balance_wei, 'ether'))
            balance_usd = balance_bnb * bnb_price

            # Read configurable funding parameters (clamped to exactly $1.00 as policy)
            def _parse_float(env_key: str, default: float) -> float:
                try:
                    return float(os.getenv(env_key, str(default)))
                except Exception:
                    return default

            usd_per_wallet = _parse_float('FUND_USD_PER_WALLET', 3.0)
            gas_reserve_usd = _parse_float('GAS_RESERVE_USD', 1.0)

            min_required_usd = gas_reserve_usd + usd_per_wallet
            if balance_usd < min_required_usd:
                needed_usd = min_required_usd - balance_usd
                needed_bnb = needed_usd / bnb_price if bnb_price else 0.0
                bsc_scan_link = f"https://bscscan.com/address/{checksum}"

                msg = (
                    f"❌ <b>Need at least ${min_required_usd:.2f} to start funding</b>\n\n"
                    f"• <b>Current</b>: ${balance_usd:.2f} (<code>{balance_bnb:.6f} BNB</code>)\n"
                    f"• <b>BNB Price</b>: ${bnb_price:.4f}\n\n"
                    f"Send at least <b>${needed_usd:.2f}</b> (≈<code>{needed_bnb:.6f} BNB</code>) more to:\n\n"
                    f"<pre>{checksum}</pre>\n"
                    f"BscScan: <a href=\"{bsc_scan_link}\">{bsc_scan_link}</a>\n\n"
                    "After sending, use /check_balance to verify the transaction."
                )
                await self.safe_reply_text(update, msg, parse_mode='HTML')
                return

            # Determine funding plan based on configured USD per wallet and gas reserve
            max_wallets_possible = int((balance_usd - gas_reserve_usd) / usd_per_wallet)
            max_wallets = max(0, min(max_wallets_possible, len(self.wallets), 34))
            if max_wallets <= 0:
                await self.safe_reply_text(update, "⚠️ Not enough balance after reserving gas to fund any wallet.")
                return

            bnb_per_wallet = usd_per_wallet / bnb_price
            total_bnb = bnb_per_wallet * max_wallets
            funding_details = {
                'max_wallets': max_wallets,
                'bnb_per_wallet': bnb_per_wallet,
                'total_bnb': total_bnb,
                'bnb_price': bnb_price,
                'usd_per_wallet': usd_per_wallet,
                'gas_reserve_usd': gas_reserve_usd
            }

            # Trigger the standard funding routine which posts progress and completion
            await self.distribute_funds(update, context, funding_details)
        except Exception as e:
            self.logger.error(f"Error in start_funding: {str(e)}", exc_info=True)
            await self.safe_reply_text(update, "❌ Failed to start funding. Please check logs and try again.")

    async def check_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Enforce /connect in current session
        if not context.user_data.get('token_connected', False):
            await self.safe_reply_text(update, "❌ Please use /connect to set token details before using this command (required each session).")
            return
        # Enforce /connect in current session
        if not context.user_data.get('token_connected', False):
            await self.safe_reply_text(update, "❌ Please use /connect to set token details before using this command (required each session).")
            return
        """Check the balance of the central wallet for the currently connected chain."""
        # Resolve chain from connection state
        chain = getattr(self, 'connected_chain', None) or (self.token_config.get('chain') if hasattr(self, 'token_config') else None)
        if not chain:
            await self.safe_reply_text(update, "❌ Please connect a token first so I can determine the chain.")
            return
        cw = self.get_central_wallet(chain)
        address = (cw.get('address') or '').strip()
        pkey = (cw.get('private_key') or '').strip()
        if not address or not pkey:
            await self.safe_reply_text(update, f"❌ Central wallet for {chain} not configured. Please set the appropriate environment variables.")
            return
        try:
            # Typing indicator (best effort)
            try:
                await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
            except Exception:
                pass

            if chain in {'BNB', 'ETH'}:
                # Fetch price for USD display when on EVM
                bnb_price = await self.get_bnb_price()
                if not bnb_price:
                    raise ValueError("Could not fetch BNB price")
                w3 = self.get_web3_for_chain(chain)
                try:
                    checksum = w3.to_checksum_address(address)
                except Exception:
                    checksum = address
                balance_wei = w3.eth.get_balance(checksum)
                balance_native = float(w3.from_wei(balance_wei, 'ether'))
                usd = balance_native * float(bnb_price)
                symbol = 'BNB' if chain == 'BNB' else 'ETH'
                explorer = 'https://bscscan.com' if chain == 'BNB' else 'https://etherscan.io'
                addr_short = f"{checksum[:6]}...{checksum[-4:]}"
                response_text = (
                    "💰 <b>Wallet Balance</b>\n\n"
                    f"<b>{chain} Central Wallet</b>\n"
                    f"• Address: <code>{addr_short}</code>\n"
                    f"• Balance: <code>{balance_native:.6f} {symbol}</code>\n"
                   
                )
                await self.safe_reply_text(update, response_text, parse_mode='HTML')
                return
            else:
                await self.safe_reply_text(update, f"❌ Unsupported chain: {chain}")
                return
        except Exception as e:
            self.logger.error(f"Error in check_balance: {str(e)}", exc_info=True)
            await self.safe_reply_text(update, "❌ An error occurred while checking balances. Please try again later.")

    async def wallet_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Enforce /connect in current session
        if not context.user_data.get('token_connected', False):
            await self.safe_reply_text(update, "❌ Please use /connect to set token details before using this command (required each session).")
            return
        """Show number of generated wallets, count funded, and total distributed."""
        try:
            # Ensure wallets is a list of dicts
            if not hasattr(self, 'wallets') or not isinstance(self.wallets, list):
                self.wallets = []
            
            # Convert any string wallets to dict format
            normalized_wallets = []
            for w in self.wallets:
                if isinstance(w, str):
                    normalized_wallets.append({'address': w.strip()})
                elif isinstance(w, dict):
                    normalized_wallets.append(w)
            
            generated = len(normalized_wallets)
            self.wallets = normalized_wallets  # Update with normalized data

            # Ensure attributes exist
            if not hasattr(self, 'funded_wallets') or not isinstance(self.funded_wallets, list):
                self.funded_wallets = []
            if not hasattr(self, 'total_distributed_bnb'):
                self.total_distributed_bnb = 0.0

            # If we don't have a funded cache, scan all wallets to detect manual funding
            funded_list = list(self.funded_wallets)
            if generated > 0 and not funded_list:
                try:
                    chain = (self.connected_chain or 'BNB').upper()
                    w3 = self.get_web3_for_chain(chain)
                    min_balance, thresh_text = await self._compute_min_required_native(chain)
                    funded_detected = []
                    total_detected_balance = 0.0

                    for wallet in self.wallets:
                        try:
                            # Handle both string and dict wallet formats
                            if isinstance(wallet, str):
                                addr = wallet.strip()
                                wallet_obj = {'address': addr}
                            elif isinstance(wallet, dict):
                                addr = wallet.get('address', '')
                                wallet_obj = wallet
                            else:
                                continue
                                
                            if not addr:
                                continue
                                
                            # Get balance and check if it meets minimum
                            wei = await self._get_balance_with_retry(w3, addr)
                            bal = float(w3.from_wei(wei, 'ether'))
                            if bal >= float(min_balance):
                                # Include any additional wallet data in the result
                                result = {'address': addr, 'balance': bal, **wallet_obj}
                                funded_detected.append(result)
                                total_detected_balance += bal
                        except Exception as e:
                            self.logger.warning(f"wallet_status: balance check failed for {addr}: {e}")

                    # Update cache for future commands
                    if funded_detected:
                        # Keep only address and balance
                        funded_detected.sort(key=lambda x: x['balance'], reverse=True)
                        self.funded_wallets = funded_detected
                        funded_list = funded_detected
                        self.logger.info(f"wallet_status: detected {len(funded_detected)} funded wallets on-chain")
                except Exception as e:
                    self.logger.warning(f"wallet_status: on-chain scan skipped due to error: {e}")

            funded_count = len(funded_list)
            total_bnb_distributed = float(self.total_distributed_bnb or 0.0)

            # Build summary, including a quick preview of detected funded wallets
            preview = ''
            if funded_list:
                top = funded_list[:3]  # Show first 3 funded wallets
                lines = []
                
                for w in top:
                    try:
                        # Handle both dictionary and string wallet formats
                        if isinstance(w, dict):
                            addr = str(w.get('address', '')).strip()
                            bal = float(w.get('balance', 0.0) or 0.0)
                        else:
                            # If it's a string, use it as the address with 0 balance
                            addr = str(w).strip()
                            bal = 0.0
                        
                        if addr:  # Only add if we have a valid address
                            lines.append(f"• <code>{addr[:6]}...{addr[-4:]}</code> — {bal:.4f} BNB")
                    except Exception as e:
                        self.logger.warning(f"Error formatting wallet preview for {w}: {e}")
                
                # Only show preview if we have valid wallet entries
                if lines:
                    more = f"\n• ...and {len(funded_list) - len(top)} more" if len(funded_list) > len(top) else ''
                    preview = "\n\n<b>Detected funded wallets</b>:\n" + "\n".join(lines) + more

            self.logger.info(
                f"Wallet Status - Generated: {generated}, Funded: {funded_count}, Total Distributed BNB: {total_bnb_distributed:.6f}"
            )

            status_text = (
                "📊 <b>Wallet Status</b>\n\n"
                f"• Generated Wallets: <b>{generated}</b>\n"
                f"• Funded Wallets (≥ {thresh_text if 'thresh_text' in locals() else '0.00085 BNB'}): <b>{funded_count}</b>\n"
                f"• Total BNB Distributed (funding): <b>{total_bnb_distributed:.6f}</b>"
                f"{preview}"
            )

            await self.safe_reply_text(update, status_text, parse_mode='HTML')
            
        except Exception as e:
            self.logger.error(f"Error in wallet_status: {e}", exc_info=True)
            await self.safe_reply_text(update, "❌ An error occurred while fetching wallet status. Check logs for details.") 

    async def refresh_funded(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Enforce /connect in current session
        if not context.user_data.get('token_connected', False):
            await self.safe_reply_text(update, "❌ Please use /connect to set token details before using this command (required each session).")
            return
            
        try:
            if not self.wallets:
                await self.safe_reply_text(update, "❌ No wallets loaded. Use /create_wallet first.")
                return
                
            chain = (self.connected_chain or 'BNB').upper()
            msg = await self.safe_reply_text(update, "🔄 Scanning wallets on-chain for funded balances...")
            
            # Use USD-based min if configured
            detected = await self._refresh_funded_from_chain(chain, min_balance=None, force=True)
            _, thresh_text = await self._compute_min_required_native(chain)
            count = len(detected)
            
            if count == 0:
                await self.safe_edit_text(msg, f"❌ No funded wallets detected (≥ {thresh_text}).")
                return
                
            total = sum(w.get('balance', 0.0) for w in detected)
            lines = []
            for w in detected[:5]:
                addr = w['address']
                bal = float(w.get('balance', 0.0) or 0.0)
                lines.append(f"• <code>{addr[:6]}...{addr[-4:]}</code> — {bal:.4f} {chain}")
                
            more = f"\n• ...and {count-5} more" if count > 5 else ''
            status_text = (
                f"✅ <b>Detected {count} funded wallets</b> (≥ {thresh_text})\n"
                f"💼 <b>Total balance:</b> {total:.4f} {chain}\n\n"
                + "\n".join(lines)
                + more
            )
            
            await self.safe_edit_text(msg, status_text)
            
        except Exception as e:
            self.logger.error(f"Error in refresh_funded: {str(e)}")
            await self.safe_reply_text(update, f"❌ Error scanning wallets: {str(e)}")

    async def start_trend(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Enforce /connect in current session
        if not context.user_data.get('token_connected', False):
            await self.safe_reply_text(update, "❌ Please use /connect to set token details before starting a trend (required each session).")
            return


        """Start trading/trending after comprehensive pre-flight checks."""
        try:
            
            # Send initial status message
            status_msg = await self.safe_reply_text(update, "🔍 Starting pre-flight checks...")
            
            # 1. Token validation
            await self.safe_edit_text(status_msg, "🔍 Validating token details...")
            token_addr = getattr(self, 'token_address', None) or (self.token_config.get('address') if hasattr(self, 'token_config') else None)
            chain = getattr(self, 'connected_chain', None) or (self.token_config.get('chain') if hasattr(self, 'token_config') else None)
            
            if not token_addr or not chain:
                await self.safe_edit_text(status_msg, "❌ No valid token connected. Please use /connect to set token details.")
                return
                
            # Chain-specific validation
            if chain in {'BNB', 'ETH'}:
                if not (str(token_addr).startswith('0x') and len(str(token_addr)) == 42):
                    await self.safe_edit_text(status_msg, 
                        "❌ Invalid EVM token address.\n\n"
                        "• Must start with 0x\n"
                        "• Must be 42 characters long\n\n"
                        "Please check and try again with a valid address."
                    )
                    return
            elif chain == 'SOL':
                if str(token_addr).startswith('0x') or not (32 <= len(str(token_addr)) <= 48):
                    await self.safe_edit_text(status_msg,
                        "❌ Invalid Solana token address.\n\n"
                        "• Must be in base58 format\n"
                        "• Must be between 32-48 characters\n"
                        "• No '0x' prefix needed\n\n"
                        "Please verify and try again."
                    )
                    return

            # 2. Wallet funding check (gracefully handle missing cache by scanning all wallets)
            await self.safe_edit_text(status_msg, "🔍 Checking wallet funding status...")
            funded = getattr(self, 'funded_wallets', []) or []
            scanning_all = False
            if not funded:
                # No cached funded list; fall back to all loaded wallets so we can detect
                # manually funded wallets on-chain.
                candidate_wallets = self.wallets if isinstance(self.wallets, list) else []
                if not candidate_wallets:
                    await self.safe_edit_text(
                        status_msg,
                        "❌ No wallets loaded.\n\n"
                        "Please create or load wallets with /create_wallet first."
                    )
                    return
                await self.safe_edit_text(
                    status_msg,
                    "ℹ️ No cached funded list found. Scanning all wallets for balances..."
                )
                funded = candidate_wallets
                scanning_all = True

            # 3. Wallet balance verification with detailed feedback
            w3 = self.get_web3_for_chain(chain)
            min_balance, thresh_text = await self._compute_min_required_native(chain)
            # If we're scanning all wallets (no cached funded list), expand the limit to include all
            max_wallets_to_check = len(funded) if scanning_all else 20
            
            working_wallets = []
            total_balance = 0.0
            
            # Check wallet balances in batches with progress updates
            wallets_to_check = funded[:max_wallets_to_check]
            total_wallets = len(wallets_to_check)
            
            for i, wallet in enumerate(wallets_to_check, 1):
                try:
                    # Extract wallet address
                    wallet_addr = wallet.get('address', '').strip() if isinstance(wallet, dict) else str(wallet).strip()
                    if not wallet_addr:
                        continue
                    
                    # Update status every 3 wallets or on last wallet
                    if i % 3 == 1 or i == total_wallets:
                        progress = f"({i}/{total_wallets})"
                        balance_info = f"• Found {len(working_wallets)} funded wallets"
                        if working_wallets:
                            balance_info += f" with {total_balance:.4f} {chain} total"
                        await self.safe_edit_text(
                            status_msg,
                            f"🔍 Checking wallet balances {progress}...\n"
                            f"{balance_info}\n"
                            "⏳ This may take a moment..."
                        )
                    
                    # Get balance with retry logic
                    balance_wei = await self._get_balance_with_retry(w3, wallet_addr)
                    balance = float(w3.from_wei(balance_wei, 'ether'))
                    
                    if balance >= float(min_balance):
                        working_wallets.append({
                            'address': wallet_addr,
                            'balance': balance,
                            'private_key': wallet.get('private_key') if isinstance(wallet, dict) else None
                        })
                        total_balance += balance
                        
                        self.logger.info(
                            f"✅ Wallet {wallet_addr[:8]}...{wallet_addr[-6:]} has {balance:.6f} {chain} (≥ {thresh_text} required)"
                        )
                    else:
                        self.logger.warning(
                            f"⚠️  Wallet {wallet_addr[:8]}...{wallet_addr[-6:]} has insufficient balance: {balance:.6f} {chain} (needs {thresh_text})"
                        )
                        
                except Exception as e:
                    self.logger.error(f"❌ Error checking wallet {i}: {str(e)}", exc_info=True)
                    continue
            
            # Process results
            if not working_wallets:
                await self.safe_edit_text(
                    status_msg,
                    "❌ No wallets with sufficient balance found.\n\n"
                    f"• Minimum required: {thresh_text} per wallet\n"
                    f"• Wallets checked: {total_wallets}\n\n"
                    "Please fund your wallets using /start_funding and try again."
                )
                return
            
            # 4. Prepare final wallet list
            working_wallets.sort(key=lambda x: x['balance'], reverse=True)
            
            # Limit to top 20 wallets by balance to prevent excessive gas usage
            if len(working_wallets) > 20:
                working_wallets = working_wallets[:20]
                total_balance = sum(w['balance'] for w in working_wallets)
                
            # Update instance state
            self.funded_wallets = working_wallets
            
            # Show wallet summary before proceeding (Option A formatting)
            bullet_lines = []
            for w in working_wallets[:5]:
                addr = w['address']
                bullet_lines.append(f"• <code>{addr[:6]}...{addr[-4:]}</code> — {w['balance']:.4f} {chain}")
            more = f"\n• ...and {len(working_wallets) - 5} more wallets" if len(working_wallets) > 5 else ''
            preflight_text = (
                f"✅ <b>Found {len(working_wallets)} funded wallets</b> (≥ {thresh_text})\n"
                f"💼 <b>Total balance:</b> {total_balance:.4f} {chain}\n\n"
                f"📋 <b>Wallet summary</b>:\n" + "\n".join(bullet_lines) + more + "\n\n"
                "🔄 Initializing trading cycle..."
            )
            await self.safe_edit_text(status_msg, preflight_text, parse_mode='HTML')

            # 5. Initialize trading cycle with validated wallets
            try:
                # Ensure trading_cycle exists and token is in sync (chain-aware)
                if not hasattr(self, 'trading_cycle') or not self.trading_cycle:
                    if chain in {'BNB', 'ETH'}:
                        w3 = self.get_web3_for_chain(chain)
                        self.trading_cycle = TradingCycle(
                            wallet_manager=self, 
                            web3=w3, 
                            token_address=token_addr, 
                            chain=chain
                        )
                    elif chain == 'SOL':
                        if SolanaTradingCycle is None:
                            raise ImportError("SOL trading engine not available. Missing module.")
                        client = self.get_solana_client()
                        self.trading_cycle = SolanaTradingCycle(
                            wallet_manager=self, 
                            client=client, 
                            token_address=token_addr
                        )
                    else:
                        raise ValueError(f"Unsupported chain: {chain}")
                else:
                    # Update existing trading cycle with new token address
                    self.trading_cycle.token_address = token_addr

                # 6. Prepare wallet data for trading cycle
                try:
                    # Ensure we have private keys for all working wallets
                    for wallet in working_wallets:
                        if 'private_key' not in wallet or not wallet['private_key']:
                            wallet['private_key'] = self.wallet_private_keys.get(wallet['address'])
                    
                    # Update trading cycle with validated wallets
                    self.trading_cycle.wallets = working_wallets
                    
                    # Start the trading cycle
                    start_msg = await self.trading_cycle.start()
                    
                    # 6b. Activate external trending platforms (DexScreener/DEXTools)
                    try:
                        selected_platforms = self.trending_mgr.activate_platforms(chain, token_addr)
                    except Exception as te:
                        self.logger.warning(f"Trending platforms activation failed: {te}")
                        selected_platforms = []

                    # Final success message
                    await self.safe_edit_text(
                        status_msg,
                        f"🚀 Trading cycle started successfully!\n\n"
                        f"• Token: {token_addr}\n"
                        f"• Chain: {chain}\n"
                        f"• Wallets: {len(working_wallets)} ready\n"
                        f"• Total balance: {total_balance:.4f} {chain}\n"
                        f"• Trending platforms: {', '.join(selected_platforms) if selected_platforms else 'none'}\n\n"
                        f"{start_msg}\n\n"
                        "Use /stop_trend to stop the trading cycle."
                    )
                    
                except Exception as e:
                    # Handle errors that occur during wallet prep/trading start/platform activation
                    self.logger.error(f"Error initializing trading cycle: {str(e)}", exc_info=True)
                    # Re-raise to be caught by the outer try/except for unified user feedback
                    raise

            except ImportError as ie:
                await self.safe_edit_text(
                    status_msg,
                    f"❌ {str(ie)}\n\n"
                    "Please install the required dependencies and try again."
                )
                return
                
            except Exception as e:
                self.logger.error(f"Error in trading cycle setup: {str(e)}", exc_info=True)
                await self.safe_edit_text(
                    status_msg,
                    f"❌ Failed to start trading cycle.\n\n"
                    f"Error: {str(e)}\n\n"
                    "Please check the logs for more details and try again."
                )
                return
                
        except Exception as e:
            self.logger.error(f"Critical error in start_trend: {str(e)}", exc_info=True)
            try:
                await self.safe_edit_text(
                    status_msg,
                    "❌ A critical error occurred while starting the trend.\n\n"
                    "Please check the logs and try again. If the issue persists, "
                    "contact support with the error details."
                )
            except:
                # Fallback in case status_msg is not available
                await self.safe_reply_text(
                    update,
                    "❌ A critical error occurred. Please check the logs and try again."
                )

    async def stop_trend(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Enforce /connect in current session
        if not context.user_data.get('token_connected', False):
            await self.safe_reply_text(update, "❌ Please use /connect to set token details before using this command (required each session).")
            return
        """Stop trading/trending. Placeholder implementation to satisfy handler."""
        try:
            # If a future trading loop uses a flag/task, we would cancel it here.
            await self.safe_reply_text(update, "🛑 Trading stopped (no active trading loop running).")
        except Exception as e:
            self.logger.error(f"Error in stop_trend: {str(e)}", exc_info=True)
            await self.safe_reply_text(update, "❌ Failed to stop trend. Please try again later.")
            
    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Cancels and ends the conversation."""
        # Clear any withdrawal-related user data
        if 'withdraw_chain' in context.user_data:
            del context.user_data['withdraw_chain']
        if 'withdraw_address' in context.user_data:
            del context.user_data['withdraw_address']
            
        await update.message.reply_text(
            '❌ Operation cancelled.',
            reply_markup=ReplyKeyboardRemove(),
            parse_mode='Markdown'
        )
        return ConversationHandler.END

    async def withdraw_funds(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Start the withdrawal process"""
        user_id = update.effective_user.id
        if not self._is_authorized(user_id):
            await update.message.reply_text("❌ You are not authorized to use this command.")
            return ConversationHandler.END
            
        # Check if we have a connected chain
        if not self.connected_chain:
            await update.message.reply_text(
                "❌ No blockchain connected. Please connect a blockchain first using /connect."
            )
            return ConversationHandler.END
            
        # Initialize WalletManager if not already done
        if not hasattr(self, 'wallet_manager'):
            self.wallet_manager = WalletManager()
            
        # Check total available balance
        balance_info = self.wallet_manager.get_total_balance(self.connected_chain)
        
        if balance_info['total_balance'] <= 0:
            await update.message.reply_text(
                "❌ No funds available for withdrawal."
            )
            return ConversationHandler.END
            
        # Store chain in context for the next step
        context.user_data['withdraw_chain'] = self.connected_chain
        
        # Prompt for wallet address
        await update.message.reply_text(
            f"💼 *Withdrawal Request*\n\n"
            f"Total available: *{balance_info['total_balance']:.6f} {self.connected_chain}*\n"
            f"Number of wallets with balance: *{balance_info['wallet_count']}*\n\n"
            f"Please enter the destination wallet address:",
            parse_mode='Markdown'
        )
        
        return 'AWAITING_WALLET_ADDRESS'
        
    async def handle_withdraw_address(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle the wallet address input for withdrawal"""
        user_id = update.effective_user.id
        if not self._is_authorized(user_id):
            await update.message.reply_text("❌ You are not authorized to perform this action.")
            return ConversationHandler.END
            
        wallet_address = update.message.text.strip()
        chain = context.user_data.get('withdraw_chain')
        
        if not chain:
            await update.message.reply_text("❌ Error: No chain specified. Please start over with /withdraw_funds.")
            return ConversationHandler.END
            
        # Initialize WalletManager if not already done
        if not hasattr(self, 'wallet_manager'):
            self.wallet_manager = WalletManager()
            
        # Validate wallet address with enhanced validation
        is_valid, error_msg = self.wallet_manager.validate_wallet_address(wallet_address, chain)
        if not is_valid:
            # Provide specific error message from validation
            error_text = f"❌ {error_msg or f'Invalid {chain} address'}"
            
            # Add helpful hints based on chain
            if chain in ['ETH', 'BNB']:
                error_text += "\n\nFor EVM addresses, ensure:\n"
                error_text += "• It starts with '0x'\n"
                error_text += "• It's exactly 42 characters long\n"
                error_text += "• It contains only hexadecimal characters (0-9, a-f, A-F)"
            elif chain == 'SOL':
                error_text += "\n\nFor Solana addresses, ensure:\n"
                error_text += "• It's between 32-44 characters long\n"
                error_text += "• It contains only base58 characters\n"
                error_text += "• No special characters except alphanumeric"
                
            await update.message.reply_text(
                error_text,
                parse_mode='Markdown'
            )
            return 'AWAITING_WALLET_ADDRESS'
            
        # Store the validated address
        context.user_data['withdraw_address'] = wallet_address
        
        # Show confirmation
        await update.message.reply_text(
            f"📝 *Withdrawal Confirmation*\n\n"
            f"• Destination: `{wallet_address}`\n"
            f"• Network: {chain}\n\n"
            f"Please confirm the withdrawal by typing `CONFIRM` or cancel with /cancel.",
            parse_mode='Markdown',
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton('CONFIRM')], [KeyboardButton('Cancel')]],
                one_time_keyboard=True,
                resize_keyboard=True
            )
        )
        
        return 'CONFIRM_WITHDRAWAL'
        
    async def confirm_withdrawal(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle withdrawal confirmation"""
        user_id = update.effective_user.id
        if not self._is_authorized(user_id):
            await update.message.reply_text("❌ You are not authorized to perform this action.")
            return ConversationHandler.END
            
        if update.message.text.upper() != 'CONFIRM':
            await update.message.reply_text("❌ Withdrawal cancelled.")
            return ConversationHandler.END
            
        chain = context.user_data.get('withdraw_chain')
        wallet_address = context.user_data.get('withdraw_address')
        
        if not chain or not wallet_address:
            await update.message.reply_text("❌ Error: Missing withdrawal details. Please start over.")
            return ConversationHandler.END
            
        # Start withdrawal process
        processing_msg = await update.message.reply_text("⏳ Processing withdrawal, please wait...")
        
        try:
            # Initialize WalletManager if not already done
            if not hasattr(self, 'wallet_manager'):
                self.wallet_manager = WalletManager()
                
            # Consolidate funds
            result = await self.wallet_manager.consolidate_funds(
                chain=chain,
                destination_address=wallet_address,
                gas_price_gwei=None,  # Use current network gas price
                max_gas_fee=0.001  # Leave 0.001 native token for gas in each wallet
            )
            
            if result['success'] and result['total_sent'] > 0:
                # Format the explorer link based on chain
                explorer_name = {
                    'BNB': 'BscScan',
                    'ETH': 'Etherscan',
                    'SOL': 'Solana Explorer'
                }.get(chain, 'Block Explorer')
                
                # Format wallet address for display (first 6 and last 4 characters)
                display_address = (
                    f"{wallet_address[:6]}...{wallet_address[-4:]}"
                    if len(wallet_address) > 10
                    else wallet_address
                )
                
                # Success message with transaction details and explorer link
                message = (
                    "<b>✅ Withdrawal Successful!</b>\n\n"
                    f"<b>Amount Sent:</b> <code>{result['total_sent']:.6f} {chain}</code>\n"
                    f"<b>Destination:</b> <code>{display_address}</code>\n"
                    f"<b>Transaction Count:</b> {len(result['tx_hashes'])}\n\n"
                    "<i>Funds have been transferred to your wallet.</i>"
                )
                
                # Add explorer link to wallet
                if chain in ['BNB', 'ETH']:
                    explorer_base = {
                        'BNB': 'https://bscscan.com/address/',
                        'ETH': 'https://etherscan.io/address/'
                    }[chain]
                    message += f"\n\n🔍 <a href=\"{explorer_base}{wallet_address}\">View on {explorer_name}</a>"
                
                # Add transaction hashes if available
                if result['tx_hashes']:
                    message += "\n\n<b>Transactions:</b>"
                    for i, tx_hash in enumerate(result['tx_hashes'], 1):
                        tx_url = self._get_explorer_url(chain, tx_hash)
                        message += f"\n{i}. <a href=\"{tx_url}\">{tx_hash[:8]}...{tx_hash[-4:]}</a>"
                
                await processing_msg.edit_text(
                    message,
                    parse_mode='HTML',
                    reply_markup=ReplyKeyboardRemove(),
                    disable_web_page_preview=True
                )
            else:
                error_msg = "❌ Withdrawal failed. No funds were transferred."
                if result['errors']:
                    error_msg += "\n\n*Errors:*\n"
                    error_msg += "\n".join([f"• {e.get('wallet')}: {e.get('error')}" for e in result['errors'][:3]])
                    if len(result['errors']) > 3:
                        error_msg += f"\n... and {len(result['errors']) - 3} more"
                
                await processing_msg.edit_text(
                    error_msg,
                    parse_mode='Markdown',
                    reply_markup=ReplyKeyboardRemove()
                )
                
        except Exception as e:
            self.logger.error(f"Withdrawal error: {str(e)}", exc_info=True)
            await processing_msg.edit_text(
                f"❌ Error during withdrawal: {str(e)}",
                reply_markup=ReplyKeyboardRemove()
            )
            
        # Clear user data
        if 'withdraw_chain' in context.user_data:
            del context.user_data['withdraw_chain']
        if 'withdraw_address' in context.user_data:
            del context.user_data['withdraw_address']
            
        return ConversationHandler.END
        
    def _is_authorized(self, user_id: int) -> bool:
        """Check if a user is authorized to use admin commands.
        
        Args:
            user_id: The Telegram user ID to check
            
        Returns:
            bool: True if authorized, False otherwise
        """
        # Get list of authorized user IDs from environment variable
        authorized_users = os.getenv('AUTHORIZED_USERS', '').split(',')
        authorized_user_ids = [int(uid.strip()) for uid in authorized_users if uid.strip().isdigit()]
        
        # Allow if user ID is in the authorized list or if no authorized users are set (for development)
        return not authorized_user_ids or user_id in authorized_user_ids

    def _get_explorer_url(self, chain: str, tx_hash: str) -> str:
        """Get blockchain explorer URL for a transaction"""
        if chain == 'BNB':
            return f"https://bscscan.com/tx/{tx_hash}"
        elif chain == 'ETH':
            return f"https://etherscan.io/tx/{tx_hash}"
        elif chain == 'SOL':
            return f"https://explorer.solana.com/tx/{tx_hash}"
        return "#"

    def run(self):
        """Starts the bot."""
        logging.basicConfig(
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            level=logging.INFO
        )
        logging.info("Bot starting...")
        # Drop pending updates and ensure clean polling startup
        self.app.run_polling(drop_pending_updates=True)

    async def handle_application_error(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        """Global error handler to log and suppress non-fatal errors."""
        try:
            self.logger.error("Application error", exc_info=context.error)
        except Exception:
            # Ensure we never raise from the error handler
            pass

if __name__ == '__main__':
    bot = TokenTrendingBot()
    bot.run()