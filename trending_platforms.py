import os
import time
import logging
import threading
import requests
from typing import List, Dict

SUPPORTED_CHAINS = {
    'BNB': 'bsc',
    'ETH': 'ethereum',
    'SOL': 'solana',  # used only for routing; dexscreener supports solana
}

class TrendingPlatformManager:
    """Lightweight integration for DexScreener and DEXTools.

    - DexScreener: use public API to fetch pairs and warm caches.
    - DEXTools: warm public pair pages using pairs discovered from DexScreener.

    This does NOT purchase paid trending; it optimizes organic visibility.
    """

    def __init__(self, logger: logging.Logger | None = None):
        self.logger = logger or logging.getLogger(__name__)
        # env feature toggles
        self.enable_dexscreener = (os.getenv('ENABLE_DEXSCREENER', '1') != '0')
        self.enable_dextools = (os.getenv('ENABLE_DEXTOOLS', '1') != '0')

    def _dexscreener_token_url(self, token_address: str) -> str:
        return f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"

    def _dexscreener_pair_url(self, chain: str, pair_address: str) -> str:
        chain_slug = SUPPORTED_CHAINS.get((chain or 'BNB').upper(), 'bsc')
        return f"https://api.dexscreener.com/latest/dex/pairs/{chain_slug}/{pair_address}"

    def _dextools_pair_page(self, chain: str, pair_address: str) -> str:
        # Map chain to DEXTools route segment
        ch = (chain or 'BNB').upper()
        if ch == 'BNB':
            seg = 'bnb'
        elif ch == 'ETH':
            seg = 'ether'
        elif ch == 'SOL':
            seg = 'solana'
        else:
            seg = 'bnb'
        return f"https://www.dextools.io/app/en/{seg}/pair-explorer/{pair_address}"

    def fetch_pairs_from_dexscreener(self, chain: str, token_address: str) -> List[Dict]:
        if not self.enable_dexscreener:
            return []
        try:
            resp = requests.get(self._dexscreener_token_url(token_address), timeout=10)
            if resp.status_code != 200:
                self.logger.debug(f"DexScreener token fetch non-200: {resp.status_code}")
                return []
            data = resp.json() or {}
            pairs = data.get('pairs') or []
            # Filter by chain if provided
            ch = (chain or '').upper()
            if ch:
                chain_slug = SUPPORTED_CHAINS.get(ch)
                if chain_slug:
                    pairs = [p for p in pairs if (p.get('chainId') == chain_slug or p.get('chainId') == ch.lower())]
            return pairs
        except Exception as e:
            self.logger.warning(f"DexScreener fetch pairs failed: {e}")
            return []

    def warm_platforms(self, chain: str, token_address: str):
        """Background job: warm DexScreener APIs and DEXTools pages for discovered pairs."""
        def _run():
            try:
                pairs = self.fetch_pairs_from_dexscreener(chain, token_address)
                if self.enable_dexscreener:
                    # Warm token endpoint
                    try:
                        _ = requests.get(self._dexscreener_token_url(token_address), timeout=10)
                    except Exception:
                        pass
                    # Warm pair endpoints
                    for p in pairs[:6]:  # limit to avoid hammering
                        pair_addr = p.get('pairAddress') or p.get('pairId') or ''
                        if not pair_addr:
                            continue
                        try:
                            _ = requests.get(self._dexscreener_pair_url(chain, pair_addr), timeout=10)
                        except Exception:
                            pass
                        time.sleep(0.7)
                if self.enable_dextools:
                    # Warm DEXTools pair pages for visibility
                    for p in pairs[:6]:
                        pair_addr = p.get('pairAddress') or p.get('pairId') or ''
                        if not pair_addr:
                            continue
                        url = self._dextools_pair_page(chain, pair_addr)
                        try:
                            _ = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
                        except Exception:
                            pass
                        time.sleep(0.7)
                self.logger.info(f"Trending warm-up completed. Pairs: {len(pairs)}")
            except Exception as e:
                self.logger.warning(f"Trending warm-up error: {e}")
        t = threading.Thread(target=_run, daemon=True)
        t.start()

    def activate_platforms(self, chain: str, token_address: str, platforms: List[str] | None = None) -> List[str]:
        """Record selected platforms and trigger warm-up. Returns list of selected."""
        selected = platforms or []
        if not selected:
            if self.enable_dexscreener:
                selected.append('dexscreener')
            if self.enable_dextools:
                selected.append('dextools')
        self.logger.info(f"Activating trending platforms: {', '.join(selected) or 'none'}")
        # Start background warm-up
        self.warm_platforms(chain, token_address)
        return selected
