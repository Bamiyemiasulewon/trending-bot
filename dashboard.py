from flask import Flask, jsonify

class Dashboard:
    def __init__(self, bot):
        self.app = Flask(__name__)
        self.bot = bot
        self.setup_routes()

    def setup_routes(self):
        @self.app.route('/status')
        def status():
            return jsonify(self.bot.scheduler.get_status())

        @self.app.route('/report')
        def report():
            return jsonify(self.bot.logger.get_report())

    def run(self):
        self.app.run(host='0.0.0.0', port=5000)
