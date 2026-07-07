/**
 * Запуск uvicorn из venv с корректным путём к Python (Windows / macOS / Linux).
 * Вызывается из npm script concurrently — одно окно консоли для API + Vite.
 */
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const py =
  process.platform === "win32"
    ? path.join(root, ".venv", "Scripts", "python.exe")
    : path.join(root, ".venv", "bin", "python");

if (!fs.existsSync(py)) {
  console.error(
    "[api] Нет интерпретатора в venv. Создайте окружение: python -m venv .venv",
  );
  console.error("[api] Ожидался путь:", py);
  process.exit(1);
}

const args = [
  "-m",
  "uvicorn",
  "backend.main:app",
  "--reload",
  "--host",
  "127.0.0.1",
  "--port",
  "8000",
];

const child = spawn(py, args, {
  cwd: root,
  stdio: "inherit",
  windowsHide: false,
});

child.on("exit", (code, signal) => {
  if (signal) process.kill(process.pid, signal);
  process.exit(code ?? 1);
});
