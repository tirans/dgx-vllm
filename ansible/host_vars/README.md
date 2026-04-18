# Host Variables

Override any variable from `group_vars/all/vars.yml` on a per-host basis.

Create a directory matching the inventory hostname:

    host_vars/spark-02/vars.yml

Example — disable Gemma4 and make Qwen3.5 always warm on spark-02:

    models:
      gemma4-26b-a4b:
        enabled: false
      qwen35-35b-a3b:
        enabled: true
        min_replicas: 1

## Per-host K3s networking overrides

See `example-spark/vars.yml.example` for scaffolds that pin `k3s_node_ip`
and `k3s_flannel_iface` to a specific interface. Two scenarios are covered:

- **Single NIC, force a specific v4** — override the `ansible_default_ipv4`
  auto-detect when a boot-time second default route confuses it.
- **Dual NIC, dedicate NIC-B to cluster / pod traffic** — useful when
  experimenting with Calico / Cilium on an isolated cluster subnet.

Never commit real LAN IPs — the scaffold uses RFC 5737 documentation ranges.
Copy the example to `host_vars/<inventory-hostname>/vars.yml` and edit locally.
