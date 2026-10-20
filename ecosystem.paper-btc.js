const path = require('path');

// Paper-trading bot for BTCUSDT — same launch as supervisor/swarm_manager.py start_bot()
// Interpreter + PYTHONPATH match ecosystem.config.js so `from core.* import ...` resolves.
const VENV_PYTHON = path.join(__dirname, '.venv', 'Scripts', 'python.exe');
const ROOT = __dirname;

module.exports = {
  apps: [
    {
      name: "paper-btc",
      script: "core/main.py",
      cwd: ROOT,
      interpreter: VENV_PYTHON,
      instances: 1,
      autorestart: true,
      // config.json has empty base_ticker; main.py needs it explicitly (same as swarm_manager.start_bot)
      args: ["--config", "config.json", "--ticker", "BTCUSDT", "--paper"],
      env: {
        PYTHONUNBUFFERED: "1",
        PYTHONPATH: ROOT,
        // .env (BINANCE_API_KEY/SECRET) is loaded in-process by core/main.py via dotenv
      },
    }
  ]
};
