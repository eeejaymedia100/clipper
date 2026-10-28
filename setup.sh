#!/usr/bin/env bash
# One-time setup on a fresh Ubuntu/Debian server:   ./setup.sh
# Then link your phone:                             ./setup.sh pair <YOUR-PHONE'S-DEVICE-ID>
#
# Afterwards everything runs by itself: Syncthing keeps ~/Clipper the same on the server and the phone,
# and the clipper service turns every job file in ~/Clipper/jobs into clips in ~/Clipper/<title>/.
set -euo pipefail
cd "$(dirname "$0")"
REPO=$PWD
FOLDER=$HOME/Clipper

wait_for_syncthing() {
    for _ in $(seq 60); do
        syncthing cli show system >/dev/null 2>&1 && return 0
        sleep 1
    done
    echo "Syncthing didn't start. See:  sudo journalctl -u syncthing@$USER" >&2
    exit 1
}

if [ "${1:-}" = pair ]; then
    PHONE=${2:?Give the phone device ID:  ./setup.sh pair XXXXXXX-XXXXXXX-...}
    wait_for_syncthing
    syncthing cli config devices add --device-id "$PHONE" --name phone 2>/dev/null || true
    syncthing cli config folders clipper devices add --device-id "$PHONE" 2>/dev/null || true
    if ! syncthing cli config folders clipper devices list | grep -q "$PHONE"; then
        echo "Couldn't share the Clipper folder with $PHONE. Check the ID, or add it in Syncthing's web page." >&2
        exit 1
    fi
    echo
    echo "Done. On your phone, Syncthing will ask to add the server and then the \"Clipper\" folder:"
    echo "accept both, and pick the folder on your phone where the clips should go."
    exit 0
fi

echo "== 1/5 System tools"
sudo apt-get update -q
sudo apt-get install -y -q git ffmpeg python3 python3-venv python3-pip curl ca-certificates qrencode

echo "== 2/5 Syncthing (keeps ~/Clipper the same on the server and your phone)"
if ! command -v syncthing >/dev/null; then
    sudo mkdir -p /etc/apt/keyrings
    sudo curl -fsSL -o /etc/apt/keyrings/syncthing-archive-keyring.gpg https://syncthing.net/release-key.gpg
    echo "deb [signed-by=/etc/apt/keyrings/syncthing-archive-keyring.gpg] https://apt.syncthing.net/ syncthing stable-v2" \
        | sudo tee /etc/apt/sources.list.d/syncthing.list >/dev/null
    if ! { sudo apt-get update -q && sudo apt-get install -y -q syncthing; }; then
        echo "The Syncthing download site didn't answer; using the Ubuntu/Debian copy instead."
        sudo rm -f /etc/apt/sources.list.d/syncthing.list
        sudo apt-get update -q
        sudo apt-get install -y -q syncthing
    fi
fi

echo "== 3/5 JavaScript runtime (some sites, YouTube included, need one for good quality)"
NODE_MAJOR=$(node --version 2>/dev/null | sed 's/^v\([0-9]*\).*/\1/' || true)
if [ "${NODE_MAJOR:-0}" -lt 20 ] && ! command -v deno >/dev/null && [ ! -x "$HOME/.deno/bin/deno" ]; then
    curl -fsSL https://deno.land/install.sh | sh -s -- -y --no-modify-path
fi

echo "== 4/5 clipper"
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt -e .
mkdir -p "$FOLDER/jobs"
cp -n "$REPO/phone-how-to.txt" "$FOLDER/how-to.txt"

echo "== 5/5 Background services"
sudo systemctl enable --now "syncthing@$USER"
wait_for_syncthing
syncthing cli config folders add --id clipper --label Clipper --path "$FOLDER" 2>/dev/null || true
if ! syncthing cli config folders list | grep -qx clipper; then
    echo "Couldn't add the Clipper folder to Syncthing." >&2
    exit 1
fi

sudo tee /etc/systemd/system/clipper.service >/dev/null <<UNIT
[Unit]
Description=clipper: job files in $FOLDER/jobs become clips
After=network-online.target

[Service]
User=$USER
WorkingDirectory=$REPO
Environment=PATH=$HOME/.deno/bin:/usr/local/bin:/usr/bin:/bin
Environment=PYTHONUNBUFFERED=1
ExecStart=$REPO/.venv/bin/clipper watch $FOLDER
Restart=always
RestartSec=10
Nice=10

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now clipper
sudo systemctl restart clipper

ID=$(syncthing cli show system | python3 -c 'import json,sys; print(json.load(sys.stdin)["myID"])')
echo
echo "All set. This server's Syncthing ID is:"
echo
echo "  $ID"
echo
qrencode -t ansiutf8 "$ID" || true
echo "Next:"
echo " 1. On your phone, install Syncthing-Fork and add a device: scan the code above (or type the ID)."
echo " 2. In Syncthing-Fork, open the menu > Show device ID, and copy it."
echo " 3. Back here, run:   ./setup.sh pair <the phone's ID>"
