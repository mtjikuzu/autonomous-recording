# Network Tutorials — Tool Stack Research

> Cisco networking home lab using Docker-based tooling.

---

## Overview: How the Tools Fit Together

```
┌─────────────────────────────────────────────────────────────┐
│                        WORKFLOW                             │
│                                                             │
│  NetBox ──► NRX ──► ContainerLab topology YAML             │
│     │                      │                               │
│     │                      ▼                               │
│     │              ContainerLab deploys                     │
│     │              Cisco containers/VMs                     │
│     │                      │                               │
│     └──► NRX ──► Graphite (live topology visualization)    │
│                  ContainerLab ──► Graphite (inline node)   │
└─────────────────────────────────────────────────────────────┘
```

| Tool | Role | Docker? |
|---|---|---|
| **NetBox** | Network source of truth — inventory, IPs, topology | Yes (Docker Compose) |
| **ContainerLab** | Deploys containerized network topologies | Uses Docker |
| **NetLab** | Higher-level abstraction over ContainerLab/Vagrant | pip install |
| **NRX** | Exports NetBox topology → ContainerLab/Graphite | pip install |
| **Graphite** | Web-based topology visualizer | Docker container |

---

## 1. NetBox

### What It Is
NetBox is a network source of truth (NSOT) and IPAM/DCIM platform. For home labs, it acts as the single place where you define:
- Devices and their types/roles
- IP addresses and prefixes
- Physical/logical connections between devices
- Sites, racks, and other infrastructure context

NetBox exposes a full REST API and GraphQL interface — tools like NRX use it to pull topology data.

### Docker Setup (Official)
- **Repo**: https://github.com/netbox-community/netbox-docker
- **Image**: `docker.io/netboxcommunity/netbox:latest`
- **Latest docker release**: v4.0.0 (Feb 2026)

```bash
git clone -b release https://github.com/netbox-community/netbox-docker.git
cd netbox-docker
cp docker-compose.override.yml.example docker-compose.override.yml
docker compose pull
docker compose up
```

Access at: **http://localhost:8000**

Create first admin user:
```bash
docker compose exec netbox /opt/netbox/netbox/manage.py createsuperuser
```

### Services Deployed
The Docker Compose stack includes:
- `netbox` — main Django app
- `netbox-worker` — background task runner
- `postgres` — database
- `redis` — caching
- `nginx` — reverse proxy

### Requirements
- Docker >= 20.10.10
- docker-compose >= 1.28.0

### Useful Env Vars (in `docker-compose.override.yml`)
```yaml
services:
  netbox:
    environment:
      SUPERUSER_NAME: admin
      SUPERUSER_EMAIL: admin@example.com
      SUPERUSER_PASSWORD: yourpassword
```

### NetBox for Cisco Labs
Once running, model your lab topology:
1. Create **Device Types** (e.g., Cisco CSR1000v, IOL)
2. Create **Devices** with roles (router, switch)
3. Add **Interfaces** and **Cables** between them
4. Assign **IP addresses** per interface

This modeled topology is what NRX will export.

---

## 2. ContainerLab

### What It Is
ContainerLab is a CLI tool that deploys container-based network topologies defined in a simple YAML file. It handles:
- Container lifecycle (start, stop, destroy)
- Virtual network wiring between containers
- Management network setup
- SSH/NETCONF/gNMI access to nodes

Think of it as "Docker Compose but for network labs" — with built-in awareness of network interfaces and topology.

- **Docs**: https://containerlab.dev
- **GitHub**: https://github.com/srl-labs/containerlab
- **Latest version**: 0.73

### Installation (Arch Linux)
ContainerLab has an AUR package:
```bash
yay -S containerlab-bin
# or paru -S containerlab-bin
```

Or via the install script (works on most Linux):
```bash
bash -c "$(curl -sL https://get.containerlab.dev)"
```

Verify:
```bash
containerlab version
```

### Cisco Node Support
ContainerLab supports the following Cisco platforms:

| Kind | Description |
|---|---|
| `cisco_iol` | Cisco IOL (IOS on Linux) — lightweight, recommended for CCNA labs |
| `cisco_vios` | Cisco VIOS — similar to IOL |
| `vr-csr` | Cisco CSR 1000v — IOS-XE in a VM container |
| `xrd` | Cisco XRd — native IOS-XR container (Cisco licensing required) |
| `vr-xrv9k` | Cisco XRv 9000 — IOS-XR VM in container |
| `vr-xrv` | Cisco XRv — older IOS-XR (discontinued by Cisco) |
| `vr-n9kv` | Cisco Nexus 9000v — NX-OS in container |
| `vr-cat9kv` | Cisco Catalyst 9000v |
| `c8000` | Cisco Catalyst 8000 |
| `vr-c8000v` | Cisco C8000v — IOS-XE |
| `cisco_asav` | Cisco ASAv (firewall) |
| `cisco_sdwan` | Cisco SD-WAN |

> **Note on Cisco Images**: Most Cisco images are not freely downloadable. You'll need to obtain `.qcow2` images from Cisco (via CML license, DevNet, or VIRL) and build them into containers using [vrnetlab](https://github.com/srl-labs/vrnetlab).

### Building Cisco Container Images (vrnetlab)
```bash
git clone https://github.com/srl-labs/vrnetlab.git
cd vrnetlab/cisco/iol
# Copy your IOL image here
cp /path/to/x86_64_crb_linux.iol .
make docker-image
# Results in: vrnetlab/cisco_iol:<version>
```

### Sample Topology: 3-Router OSPF Lab
```yaml
# cisco-ospf-lab.clab.yml
name: cisco-ospf

topology:
  nodes:
    R1:
      kind: cisco_iol
      image: vrnetlab/cisco_iol:17.16.01a
    R2:
      kind: cisco_iol
      image: vrnetlab/cisco_iol:17.16.01a
    R3:
      kind: cisco_iol
      image: vrnetlab/cisco_iol:17.16.01a

  links:
    - endpoints: ["R1:eth1", "R2:eth1"]
    - endpoints: ["R2:eth2", "R3:eth1"]
    - endpoints: ["R1:eth2", "R3:eth2"]
```

Deploy:
```bash
sudo containerlab deploy -t cisco-ospf-lab.clab.yml
```

Destroy:
```bash
sudo containerlab destroy -t cisco-ospf-lab.clab.yml
```

Inspect (see IPs, status):
```bash
sudo containerlab inspect -t cisco-ospf-lab.clab.yml
```

### Default Credentials
All containerlab-deployed nodes use: `clab` / `clab@123`

Connect via SSH:
```bash
ssh clab@<container-name>
```

### Key Commands
```bash
containerlab deploy -t <file>.clab.yml     # Start lab
containerlab destroy -t <file>.clab.yml    # Stop and remove
containerlab inspect -t <file>.clab.yml    # Show node IPs/status
containerlab graph -t <file>.clab.yml      # Generate static graph
containerlab save -t <file>.clab.yml       # Save running configs
```

---

## 3. NetLab

### What It Is
NetLab (formerly netsim-tools) is a high-level abstraction layer on top of ContainerLab and libvirt/Vagrant. Instead of writing raw ContainerLab YAML, you write a simpler topology and netlab:
1. Generates the ContainerLab topology file
2. Generates Ansible inventories
3. Deploys initial device configurations (IP addressing, OSPF, BGP, VRFs, etc.) automatically

- **Docs**: https://netlab.tools
- **GitHub**: https://github.com/ipspace/netlab
- **Author**: Ivan Pepelnjak (ipSpace.net)

### When to Use NetLab vs ContainerLab Directly
| Use Case | Tool |
|---|---|
| Quick topology spin-up with auto-config | NetLab |
| Fine-grained control over topology YAML | ContainerLab |
| Learning raw device configs by hand | ContainerLab direct |
| BGP/OSPF/MPLS labs with auto-addressing | NetLab |

### Installation
```bash
pip install networklab
```

Also install ContainerLab (netlab uses it as the backend):
```bash
bash -c "$(curl -sL https://get.containerlab.dev)"
```

Install Ansible (for config deployment):
```bash
pip install ansible
```

Bootstrap the full environment (Ubuntu only — uses netlab's installer):
```bash
netlab install ubuntu containerlab ansible
```

### Supported Cisco Platforms
- Cisco IOL / IOL-XE
- Cisco CSR 1000v (IOS-XE)
- Cisco XRv / XRv 9000 (IOS-XR)
- Cisco Nexus 9000v (NX-OS)
- Cisco Cat8000v

### Sample Topology
```yaml
# ospf-lab.yml
name: ospf-lab

defaults:
  device: iosv   # default device type

nodes:
  R1:
  R2:
  R3:

links:
  - R1-R2
  - R2-R3
  - R1-R3

module: [ ospf ]
```

Deploy:
```bash
netlab up ospf-lab.yml
```

Netlab will:
1. Assign IP addresses to all interfaces
2. Generate a ContainerLab topology file
3. Start the containers
4. Push OSPF config to all devices via Ansible

Destroy:
```bash
netlab down
```

---

## 4. NetReplica NRX

### What It Is
NRX (NetReplica Exporter) is a Python CLI tool that reads your network topology from NetBox and exports it to:
- ContainerLab topology YAML
- Graphite visualization format
- Cisco CML topology
- NVIDIA Air topology
- Cytoscape JSON
- Any custom format via Jinja2 templates

- **GitHub**: https://github.com/netreplica/nrx
- **PyPI**: `pip install nrx`
- **Supports**: NetBox v4.1+, v4.2+

### The Pipeline
```
NetBox (devices + cables) ──► nrx ──► containerlab YAML
                                  └──► graphite JSON
```

### Installation
```bash
pip install nrx
```

### Configuration File (`netbox.conf`)
```ini
[netbox]
url = http://localhost:8000
api_token = your_netbox_api_token

[nrx]
# Default output format
output = containerlab
```

Get your API token from NetBox: **Admin → API Tokens → Add Token**

### Usage Examples

Export topology for ContainerLab:
```bash
nrx --config netbox.conf \
    --input netbox \
    --output containerlab \
    --site my-lab-site
```

Export for Graphite visualization:
```bash
nrx --config netbox.conf \
    --input netbox \
    --output graphite \
    --site my-lab-site \
    --tag cisco-lab
```

Filter by tag:
```bash
nrx --config netbox.conf \
    --input netbox \
    --output containerlab \
    --tag ospf-lab
```

Convert a previously saved CYJS file to ContainerLab:
```bash
nrx --input cyjs --file topology.cyjs --output containerlab
```

### Output Formats
| Flag | Output |
|---|---|
| `--output containerlab` | `<site>.clab.yml` |
| `--output graphite` | `<site>.graphite.json` |
| `--output cml` | Cisco Modeling Labs XML |
| `--output air` | NVIDIA Air topology |
| `--output cyjs` | Cytoscape JS JSON |

### Filtering Options
```bash
--site SITE     # Filter by NetBox site name
--tag TAG       # Filter by device tag
--role ROLE     # Filter by device role
```

---

## 5. Graphite (netreplica/graphite)

### What It Is
Graphite is a browser-based network topology visualizer. It renders interactive diagrams from:
- ContainerLab topology data (live)
- NRX-exported NetBox topology
- NetLab topologies

Features:
- Interactive drag-and-drop topology
- Shows live management IPs of running ContainerLab nodes
- WebSSH access to running nodes directly from the browser
- No NetBox plugin required

- **GitHub**: https://github.com/netreplica/graphite
- **Docker image**: `netreplica/graphite`

### Method 1: Embedded in ContainerLab Topology (Recommended)
Add Graphite as a node in your `.clab.yml`:

```yaml
name: cisco-ospf

topology:
  nodes:
    R1:
      kind: cisco_iol
      image: vrnetlab/cisco_iol:17.16.01a
    R2:
      kind: cisco_iol
      image: vrnetlab/cisco_iol:17.16.01a

    # Add Graphite as a visualization node
    graphite:
      kind: linux
      image: netreplica/graphite
      env:
        HOST_CONNECTION: ${SSH_CONNECTION}
      binds:
        - __clabDir__/topology-data.json:/htdocs/default/default.json:ro
        - __clabDir__/ansible-inventory.yml:/htdocs/lab/default/ansible-inventory.yml:ro
      ports:
        - 8080:80
      exec:
        - sh -c 'graphite_motd.sh 8080'
      labels:
        graph-hide: yes   # Hide Graphite from its own topology view

  links:
    - endpoints: ["R1:eth1", "R2:eth1"]
```

Deploy (note the `-E` flag to pass `SSH_CONNECTION`):
```bash
sudo -E containerlab deploy -t cisco-ospf.clab.yml
```

Graphite will be at: **http://<your-host-ip>:8080/graphite**

### Method 2: Standalone Docker Container
Useful when visualizing multiple topologies or non-running ones:
```bash
docker run -d \
  --name graphite \
  -p 8080:80 \
  -v /path/to/topology-data.json:/htdocs/default/default.json:ro \
  netreplica/graphite
```

### Method 3: Visualize NetBox Topology via NRX
```bash
# Export from NetBox to Graphite format
nrx --config netbox.conf --input netbox --output graphite --site my-site

# Run Graphite pointing at the exported JSON
docker run -d \
  --name graphite \
  -p 8080:80 \
  -v $(pwd)/my-site.graphite.json:/htdocs/default/default.json:ro \
  netreplica/graphite
```

---

## Full Stack: Getting Started Order

Recommended bring-up sequence for a complete lab environment:

### Step 1: Start NetBox
```bash
git clone -b release https://github.com/netbox-community/netbox-docker.git
cd netbox-docker
cp docker-compose.override.yml.example docker-compose.override.yml
# Edit docker-compose.override.yml to set SUPERUSER_* vars
docker compose up -d
```

### Step 2: Install ContainerLab (Arch)
```bash
yay -S containerlab-bin
```

### Step 3: Install NRX
```bash
pip install nrx
```

### Step 4: Model Your Topology in NetBox
1. Go to http://localhost:8000
2. Create a Site (e.g., "home-lab")
3. Add Device Types (Cisco IOL, CSR1000v, etc.)
4. Add Devices and assign them to the site
5. Add Interfaces and cable them together
6. Tag devices for filtering (e.g., tag: "ospf-lab")

### Step 5: Export Topology and Deploy Lab
```bash
# Create NetBox API token first, then:
nrx --config netbox.conf --input netbox --output containerlab --site home-lab
sudo containerlab deploy -t home-lab.clab.yml
```

### Step 6: Visualize with Graphite
Add the Graphite node to your `.clab.yml` (see above) or run standalone:
```bash
docker run -d -p 8080:80 \
  -v $(pwd)/.clab/topology-data.json:/htdocs/default/default.json:ro \
  netreplica/graphite
```
Browse to **http://localhost:8080/graphite**

---

## Key Resources

| Tool | Link |
|---|---|
| NetBox Docker | https://github.com/netbox-community/netbox-docker |
| ContainerLab | https://containerlab.dev |
| ContainerLab Cisco kinds | https://containerlab.dev/manual/kinds/ |
| vrnetlab (build Cisco images) | https://github.com/srl-labs/vrnetlab |
| NetLab | https://netlab.tools |
| NRX | https://github.com/netreplica/nrx |
| Graphite | https://github.com/netreplica/graphite |
| NetBox + NRX + ContainerLab demo | https://github.com/srl-labs/netbox-nrx-clab |

---

## Hardware Requirements (Home Lab)

| Setup | Min RAM | Notes |
|---|---|---|
| NetBox only | 4 GB | Postgres + Redis + Django |
| ContainerLab + IOL (3 nodes) | 6 GB | IOL is very lightweight |
| ContainerLab + CSR1000v (3 nodes) | 12 GB | Each CSR needs ~2 GB |
| ContainerLab + XRv9k (2 nodes) | 16 GB | XRv9k is RAM heavy |
| Full stack (NetBox + 3x IOL + Graphite) | 8 GB | IOL is recommended for CCNA |

> **Recommendation for starting out**: Use **Cisco IOL** nodes — they are the most resource-efficient and work well for CCNA/CCNP routing/switching labs. You will need a Cisco CML license or a DevNet sandbox to legally obtain the IOL image.
