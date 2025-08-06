import random
import time
import asyncio
from datetime import datetime, timedelta

class TwitterModule:
    def __init__(self):
        self.active_campaigns = {}
        self.engagement_texts = {
            'bullish': [
                "🚀 $TOKEN looking bullish!",
                "Strong fundamentals on $TOKEN 📈",
                "Great entry point for $TOKEN right now 🎯",
                "Keeping an eye on $TOKEN 👀",
                "$TOKEN showing amazing potential! 💎"
            ],
            'news': [
                "Big updates coming for $TOKEN! 🔥",
                "Just researched $TOKEN - impressive project!",
                "$TOKEN making moves in the market 📊",
                "New developments from $TOKEN team 🎉",
                "Market is sleeping on $TOKEN 💡"
            ],
            'technical': [
                "$TOKEN breaking key resistance 📈",
                "Beautiful chart on $TOKEN 🎯",
                "$TOKEN forming bullish pattern",
                "Strong support level for $TOKEN 💪",
                "Technical breakout on $TOKEN incoming 🚀"
            ]
        }
        
    def automate_engagement(self, hashtag, engagement_level, randomize=True):
        """Start automated Twitter engagement"""
        params = self._get_engagement_params(engagement_level)
        
        # Store campaign parameters
        self.active_campaigns[hashtag] = {
            'start_time': time.time(),
            'params': params,
            'stats': {
                'tweets': 0,
                'likes': 0,
                'retweets': 0,
                'replies': 0
            },
            'last_update': time.time()
        }
        
        # Start async engagement
        asyncio.create_task(self._run_campaign(hashtag))
        
    def _get_engagement_params(self, level):
        """Get parameters for different engagement levels"""
        base_params = {
            'low': {
                'tweets_per_hour': (2, 5),
                'likes_ratio': 0.8,
                'retweet_ratio': 0.3,
                'reply_ratio': 0.2
            },
            'medium': {
                'tweets_per_hour': (5, 10),
                'likes_ratio': 0.9,
                'retweet_ratio': 0.5,
                'reply_ratio': 0.4
            },
            'high': {
                'tweets_per_hour': (10, 20),
                'likes_ratio': 1.0,
                'retweet_ratio': 0.7,
                'reply_ratio': 0.6
            }
        }
        return base_params.get(level, base_params['medium'])
        
    def _generate_tweet(self, hashtag):
        """Generate a random tweet"""
        category = random.choice(list(self.engagement_texts.keys()))
        template = random.choice(self.engagement_texts[category])
        return template.replace('$TOKEN', hashtag)
        
    async def _run_campaign(self, hashtag):
        """Run the Twitter engagement campaign"""
        campaign = self.active_campaigns[hashtag]
        params = campaign['params']
        
        while hashtag in self.active_campaigns:
            # Calculate tweets for this interval
            tweets = random.randint(*params['tweets_per_hour'])
            interval_delay = 3600 / tweets
            
            for _ in range(tweets):
                # Generate and simulate posting tweet
                tweet_text = self._generate_tweet(hashtag)
                
                # Simulate engagement
                likes = int(random.random() < params['likes_ratio'])
                retweets = int(random.random() < params['retweet_ratio'])
                replies = int(random.random() < params['reply_ratio'])
                
                # Update stats
                campaign['stats']['tweets'] += 1
                campaign['stats']['likes'] += likes
                campaign['stats']['retweets'] += retweets
                campaign['stats']['replies'] += replies
                
                # Add random delay between tweets
                delay = random.uniform(0.8 * interval_delay, 1.2 * interval_delay)
                await asyncio.sleep(delay)
            
            campaign['last_update'] = time.time()
            
    def stop_campaign(self, hashtag):
        """Stop an active campaign"""
        if hashtag in self.active_campaigns:
            campaign = self.active_campaigns.pop(hashtag)
            return campaign['stats']
        return None
        
    def get_campaign_stats(self, hashtag):
        """Get current campaign statistics"""
        if hashtag in self.active_campaigns:
            campaign = self.active_campaigns[hashtag]
            return {
                'runtime': time.time() - campaign['start_time'],
                'tweets': campaign['stats']['tweets'],
                'likes': campaign['stats']['likes'],
                'retweets': campaign['stats']['retweets'],
                'replies': campaign['stats']['replies'],
                'engagement_rate': (campaign['stats']['likes'] + campaign['stats']['retweets'] * 2 + 
                                  campaign['stats']['replies'] * 3) / max(1, campaign['stats']['tweets']),
                'last_update': time.time() - campaign['last_update']
            }
        return None
