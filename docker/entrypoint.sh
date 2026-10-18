#!/bin/sh
# Container entrypoint: `tgo <args>` with defaults taken from the environment, so compose files and
# `docker run -e` can reshape a run without rewriting commands.
#
#   TGO_CONFIG   config file (JSON/TOML); added as --config to demo/run/config when the file exists
#   TGO_PLUGINS  comma-separated plugin modules (looked up on PYTHONPATH=/plugins); added as --plugin
#
# Anything that is not a tgo sub-command or option (python, sh, pytest ...) is executed as-is.
set -eu

case "${1:-}" in
  ""|-*|demo|run|dashboard|ontology|collect|config|ontop|offline-check|serve|live) ;;
  *) exec "$@" ;;
esac

cmd="${1:-}"
if [ $# -gt 0 ]; then shift; fi

case "$cmd" in
  demo|run|serve|config)
    if [ -n "${TGO_CONFIG:-}" ] && [ -f "$TGO_CONFIG" ]; then
      case " $* " in *" --config "*|*" --config="*) ;; *) set -- "$@" --config "$TGO_CONFIG" ;; esac
    fi ;;
esac
case "$cmd" in
  demo|run|serve)
    if [ -n "${TGO_PLUGINS:-}" ]; then
      old_ifs=$IFS; IFS=,
      for m in $TGO_PLUGINS; do
        m=$(echo "$m" | tr -d ' ')
        [ -n "$m" ] && set -- "$@" --plugin "$m"
      done
      IFS=$old_ifs
    fi ;;
esac

# -P (not -I): keep cwd off sys.path but honour PYTHONPATH=/plugins and the baked offline env.
[ -n "$cmd" ] || exec python -P -m tgo
exec python -P -m tgo "$cmd" "$@"
