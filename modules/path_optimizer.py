"""
Trading Path Optimizer Module
Optimizes trading paths and caches successful routes
"""
import logging
from typing import Dict, List, Optional, Set
from decimal import Decimal

class PathOptimizer:
    def __init__(self):
        self.logger = logging.getLogger('PathOptimizer')
        self._successful_paths: Dict[str, List[str]] = {}
        self._failed_paths: Set[str] = set()
        self._path_scores: Dict[str, float] = {}
        
    def cache_successful_path(self, token_in: str, token_out: str, path: List[str], quote: Dict):
        """Cache a successful trading path"""
        key = f"{token_in}-{token_out}"
        if key not in self._successful_paths:
            self._successful_paths[key] = []
        
        path_key = '-'.join(path)
        if path_key not in self._successful_paths[key]:
            self._successful_paths[key].append(path)
            self._path_scores[path_key] = 1.0
            
    def record_path_failure(self, path: List[str]):
        """Record a failed path"""
        path_key = '-'.join(path)
        self._failed_paths.add(path_key)
        if path_key in self._path_scores:
            self._path_scores[path_key] *= 0.8  # Reduce score on failure
            
    def get_optimized_paths(self, token_in: str, token_out: str, all_possible_paths: List[List[str]]) -> List[List[str]]:
        """Get optimized trading paths based on history"""
        key = f"{token_in}-{token_out}"
        
        # If we have successful paths, prioritize them
        if key in self._successful_paths and self._successful_paths[key]:
            successful = self._successful_paths[key]
            # Add a few alternative paths for diversity
            return successful + [p for p in all_possible_paths if p not in successful][:2]
            
        # Filter out consistently failing paths
        return [p for p in all_possible_paths if '-'.join(p) not in self._failed_paths]
        
    def reset_path_history(self):
        """Reset path history if needed"""
        self._successful_paths.clear()
        self._failed_paths.clear()
        self._path_scores.clear()
