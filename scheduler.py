from apscheduler.schedulers.background import BackgroundScheduler
import threading

class Scheduler:
    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.active_campaigns = {}
        self.lock = threading.Lock()
        self.scheduler.start()

    def start_campaign(self, token_id, func, duration=24*3600):
        with self.lock:
            if len(self.active_campaigns) < 10:
                job = self.scheduler.add_job(func, 'interval', seconds=duration, id=token_id)
                self.active_campaigns[token_id] = job

    def stop_campaign(self, token_id):
        with self.lock:
            if token_id in self.active_campaigns:
                self.scheduler.remove_job(token_id)
                del self.active_campaigns[token_id]

    def get_status(self):
        with self.lock:
            return list(self.active_campaigns.keys())
