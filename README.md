# raspberry-pi-fun

Local model development for a Raspberry Pi fleet. pi-pair is a stdlib Python router. It proxies OpenAI-style chat to Ollama or llama.cpp on pi2, pi3, and pi4.

No pip packages. It listens on port 18080.

pi2, pi3, and pi4 all sit in one custom 3D-printed server rack. Tailscale names are rpi-pi2, rpi-pi3, and rpi-pi4.

```
pi-pair/
  mini_chat.py       start here
  start.sh           same command
  pair/              peers, health, chat, stream, HTTP
  static/            chat page
  peers.example.json fleet map (copy to peers.json)
  install.sh         copy to ~/pi-pair and write a user systemd unit
  mesh-hello.sh      health, peer probes, one chat
  test_pair.py       stdlib unittest
```

`peers.json` is gitignored. `install.sh` writes it from the example only when the Pi does not already have one.

```bash
cd pi-pair
python3 mini_chat.py
```

`bash start.sh` is the same command. Bind is `0.0.0.0:18080`.

```bash
python3 -m unittest discover -s pi-pair -p 'test_*.py'
```

Fleet map, endpoints, and install steps are in `pi-pair/README.md`.
