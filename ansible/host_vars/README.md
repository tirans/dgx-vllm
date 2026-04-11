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
