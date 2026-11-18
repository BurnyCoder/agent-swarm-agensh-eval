#!/usr/bin/env bash
# Copy each finished Agensh run's records (no secrets) into runs/<name>/ for reports/collect_stats.py.
# Run as root in WSL: bash runs/fetch_artifacts.sh
# Sources: out/projects/<name>/*.json (driver and hooks), .claude-team-data/<profile>/usage/usage.jsonl
# (egress-proxy usage ledger: agent, model, status, token counts only), the delivered branch's
# first-parent git log, and the team's prompts. Profiles, tokens and service logs are not copied.
set -euo pipefail
E=/home/burny/projects/agensh-eval
A=$E/Agensh
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for name in plane-tickets pep-rag; do
    out="$A/out/projects/$name"
    [ -f "$out/delivery.json" ] || { echo "skip $name: not delivered yet"; continue; }
    dest="$HERE/$name"
    mkdir -p "$dest"
    for f in run_state.json delivery.json acceptance.json main-health.json prepare.json sync.json; do
        [ -f "$out/$f" ] && cp "$out/$f" "$dest/"
    done
    cp "$A/.claude-team-data/software-project-$name/usage/usage.jsonl" "$dest/usage.jsonl"
    cp -r "$out/prompts" "$dest/prompts"
    case "$name" in
        pep-rag) repo="$E/rag-upstream"; ref="$(python3 -c "import json;print(json.load(open('$out/delivery.json'))['branch'])")" ;;
        plane-tickets) repo="$E/plane-tickets-delivered"; rm -rf "$repo"
                       git clone -q "$out/delivery.bundle" "$repo"; ref=main ;;
    esac
    git -C "$repo" log --first-parent --format='%h|%an|%aI|%s' "$ref" > "$dest/git-log.txt"
    echo "copied $name -> $dest ($(wc -l < "$dest/usage.jsonl") usage rows)"
done
