#!/bin/sh
# Docker Engine and the NVIDIA Container Toolkit in WSL Ubuntu (handoff §4.7, ADR-0009), from their vendors' signed
# apt repositories; no Docker Desktop, no account. Run as root in the distribution:
#
#     wsl -d Ubuntu -u root -- sh deploy/wsl/install-docker.sh      (from the repository, through /mnt/c)
#
# - no NVIDIA driver is installed in Linux: the Windows driver gives CUDA to WSL (/usr/lib/wsl/lib);
# - the daemon listens on its unix socket only (never tcp://2375), and a port published without an address binds
#   127.0.0.1 (`"ip"` below): Docker writes its own firewall rules, so `-p 8765:8765` would otherwise listen everywhere;
# - systemd must be on in /etc/wsl.conf ([boot] systemd=true) for the daemon to start with the distribution.
#
# Undo: apt-get purge -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
#       nvidia-container-toolkit nvidia-container-toolkit-base libnvidia-container-tools libnvidia-container1;
#       rm -rf /var/lib/docker /var/lib/containerd /etc/docker /etc/apt/sources.list.d/docker.list
#       /etc/apt/sources.list.d/nvidia-container-toolkit.list /etc/apt/keyrings/docker.asc
#       /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
set -eu

if [ "$(id -u)" != 0 ]; then echo "run as root: wsl -d Ubuntu -u root -- sh $0" >&2; exit 1; fi
grep -q '^systemd=true' /etc/wsl.conf || { echo "systemd is off in /etc/wsl.conf: turn it on first" >&2; exit 1; }
. /etc/os-release

apt-get update -q
apt-get install -y -q ca-certificates curl gnupg

# Docker's repository (https://docs.docker.com/engine/install/ubuntu/)
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${UBUNTU_CODENAME:-$VERSION_CODENAME} stable" \
  > /etc/apt/sources.list.d/docker.list

# NVIDIA's repository (https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | gpg --batch --yes --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  > /etc/apt/sources.list.d/nvidia-container-toolkit.list

apt-get update -q
apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin \
  nvidia-container-toolkit

# the daemon: published ports on 127.0.0.1 unless an address is given; the NVIDIA runtime registered
mkdir -p /etc/docker
[ -f /etc/docker/daemon.json ] || echo '{}' > /etc/docker/daemon.json
python3 - <<'EOF'
import json
path = "/etc/docker/daemon.json"
conf = json.load(open(path))
conf["ip"] = "127.0.0.1"
conf.setdefault("log-driver", "local")
json.dump(conf, open(path, "w"), indent=2)
EOF
nvidia-ctk runtime configure --runtime=docker
systemctl enable --now docker containerd
systemctl restart docker

docker version --format 'docker {{.Server.Version}}'
docker compose version
nvidia-ctk --version | head -1
echo "listening (expect no tcp port of dockerd):"
ss -ltnp | grep -i docker || echo "  none"
