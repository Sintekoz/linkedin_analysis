#!/bin/zsh
cd "${0:A:h}"
if [[ -x .venv/bin/python ]]; then
  .venv/bin/python main.py --port 8876
else
  python3 main.py --port 8876
fi
