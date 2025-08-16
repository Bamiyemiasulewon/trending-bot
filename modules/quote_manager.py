"""
Quote Manager Module
Handles efficient quote fetching and caching
"""
import logging
import asyncio
import time
from typing import Dict, List, Optional
from decimal import Decimal

class QuoteManager:
    def __init__(self):
        self.logger = logging.getLogger('QuoteManager')
        self._quote_cache = {}
        self._quote_timestamps = {}
        self.QUOTE_CACHE_TTL = 3  # Cache quotes for 3 seconds
        
    async def get_optimized_quote(
        self,
        wallet_manager,
        token_in: str,
        token_out: str,
        amount_in: Decimal,
        path_optimizer
    ) -> Optional[Dict]:
        """Get optimized quote with caching and retry logic"""
        cache_key = f"{token_in}-{token_out}-{amount_in}"
        
        # Check cache first
        if self._is_quote_valid(cache_key):
            return self._quote_cache[cache_key]
            
        # Get optimized paths
        all_paths = wallet_manager.get_trading_paths(token_in, token_out)
        optimized_paths = path_optimizer.get_optimized_paths(token_in, token_out, all_paths)
        
        best_quote = None
        best_path = None
        
        # Try optimized paths first
        for path in optimized_paths:
            try:
                quote = await wallet_manager.get_quote_for_path(path, amount_in)
                if quote and quote.get('amount_out'):
                    if not best_quote or quote['amount_out'] > best_quote['amount_out']:
                        best_quote = quote
                        best_path = path
                        break  # Use first successful quote from optimized paths
            except Exception as e:
                path_optimizer.record_path_failure(path)
                self.logger.debug(f"Quote failed for path {path}: {str(e)}")
                continue
                
        if best_quote:
            path_optimizer.cache_successful_path(token_in, token_out, best_path, best_quote)
            self._cache_quote(cache_key, best_quote)
            return best_quote
            
        return None
        
    def _is_quote_valid(self, cache_key: str) -> bool:
        """Check if cached quote is still valid"""
        if cache_key not in self._quote_cache:
            return False
            
        age = time.time() - self._quote_timestamps.get(cache_key, 0)
        return age < self.QUOTE_CACHE_TTL
        
    def _cache_quote(self, cache_key: str, quote: Dict):
        """Cache a quote"""
        self._quote_cache[cache_key] = quote
        self._quote_timestamps[cache_key] = time.time()
        
    def clear_cache(self):
        """Clear quote cache"""
        self._quote_cache.clear()
        self._quote_timestamps.clear()
