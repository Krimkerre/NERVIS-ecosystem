# How a dashboard gate is allowed to run Node — one policy, two callers.
#
# The gates load `nervis/index.html` and execute its inline script in Node's VM
# (`nervis/tools/page_context.js` says so in its own header, escape included), so
# whatever that file contains runs with the privileges of whoever is running the
# check. `tools/check_clean_clone.sh` has wrapped that since the finding was
# first written up; `tools/githooks/pre-commit` ran the same gates with bare
# `node`, which made the every-commit path the unprotected one (base review,
# 17 September 2026, finding 5). Both now source this.
#
# `node --permission --allow-fs-read="*"` denies filesystem writes and child
# processes; the platform wrapper denies the network, which `--permission` does
# not. Neither is claimed to be a boundary against a determined escape — the
# fuller answer, parsing `index.html` instead of executing it, is the owner's
# call — but running the gates *without* them, on every commit, was the gap.
#
# Sets NODE_GUARD, an array to put in front of the gate's own arguments.
# `$1` is the path to `tools/no-network.sb` for this caller's copy of the tree.
node_guard() {
  local profile="$1"
  case "$(uname -s)" in
    Darwin)
      NODE_GUARD=(sandbox-exec -f "$profile" node --permission --allow-fs-read="*")
      ;;
    Linux)
      # Probed rather than assumed: unprivileged user namespaces are disabled on
      # some hardened or older distributions, and `true` fails there exactly the
      # way the real invocation would.
      if unshare --net --map-root-user -- true >/dev/null 2>&1; then
        NODE_GUARD=(unshare --net --map-root-user -- node --permission --allow-fs-read="*")
      else
        echo "  (Linux, but 'unshare --net --map-root-user' is not usable here — unprivileged" \
             "user namespaces may be disabled; network is not sandboxed for these gates," \
             "fs/child-process still are)"
        NODE_GUARD=(node --permission --allow-fs-read="*")
      fi
      ;;
    *)
      echo "  (neither macOS nor Linux — network is not sandboxed for these gates;" \
           "fs/child-process still are)"
      NODE_GUARD=(node --permission --allow-fs-read="*")
      ;;
  esac
}
