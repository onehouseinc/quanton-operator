---
name: clean-uninstall
description: Remove the Quanton Operator Helm release, the secrets it replicated into job namespaces, and its CRD from a Kubernetes cluster. Use whenever the user wants to uninstall, remove, tear down, reset, or clean up the quanton-operator, or reports leftover quanton-operator secrets or CRDs after a helm uninstall.
compatibility: Requires kubectl and helm on PATH, a kubeconfig context that points at the target cluster, network access to that cluster from the agent's shell, and the repository checked out so that scripts/cleanup-secrets.sh is available. Deleting the CRD needs cluster-admin rights.
allowed-tools: Bash, Read, AskUserQuestion
disable-model-invocation: true
metadata:
  version: "3"
---

# Clean uninstall of the Quanton Operator

`helm uninstall` removes the chart's own resources and leaves two things behind: the secrets
the operator copies into every job namespace, and the `QuantonSparkApplication` CRD, which Helm
never deletes by design. This skill removes all three in a fixed order, with an inventory, a
dry run, and a confirmation before anything is deleted.

Follow the working rules in `AGENTS.md`. Helper scripts live in `scripts/agent/`. Shared
failure cases live in `docs/troubleshooting.md`. This skill is destructive, so the user
invokes it by name; do not start it on your own.

## Facts this skill relies on

| Item | Value |
|---|---|
| Helm chart | `quanton-operator`. The release name and namespace are usually the same, but discover them; do not assume. |
| Replicated secrets | `quanton-operator-cert`, `quanton-operator-docker-secret`, `quanton-operator-mtls-secret` |
| CRD | `quantonsparkapplications.quantonsparkoperator.onehouse.ai` |
| Cleanup script | `scripts/cleanup-secrets.sh`. Dry run by default. `--confirm` deletes. `--namespace <ns>` narrows to one namespace. |
| Side effect of CRD deletion | Kubernetes deletes every `QuantonSparkApplication` object in the cluster, including running jobs. |

## Procedure

### Step 1: Confirm the target cluster and the tools

Run:

```bash
scripts/agent/check-cluster.sh
```

It exits 1 if `kubectl` or `helm` is missing; name the tool and stop. Otherwise it prints the
context, the first nodes, and the operator releases as `<release> <namespace> <chart> <status>`
lines. Record the `quanton-operator-*` release name and namespace, if any.

Show the context name to the user. Ask: "This removes the Quanton Operator from the cluster in
context `<name>`. Is that the cluster you mean?" Continue only on a clear yes. Uninstalling from
the wrong cluster is the worst outcome this skill can produce.

### Step 2: Inventory what exists

Run all three commands. Empty output is a valid answer, not an error.

```bash
for s in quanton-operator-cert quanton-operator-docker-secret quanton-operator-mtls-secret; do
  kubectl get secrets -A --field-selector "metadata.name=$s" --no-headers 2>/dev/null
done
kubectl get crd quantonsparkapplications.quantonsparkoperator.onehouse.ai --no-headers 2>/dev/null || echo "CRD not found"
kubectl get quantonsparkapplications -A --no-headers 2>/dev/null | wc -l
```

Record the list of secrets with their namespaces, whether the CRD exists, and the count of
`QuantonSparkApplication` objects.

If Step 1 showed no release, and there is no secret and no CRD, tell the user there is nothing
to clean up and stop.

### Step 3: Show the plan and ask once

Present the inventory as a list, using the values from Steps 1 and 2:

- Helm release `<name>` in namespace `<ns>`, or "none".
- `<N>` secrets across `<M>` namespaces, or "none".
- CRD present or absent.
- `<K>` `QuantonSparkApplication` objects that CRD deletion will also delete. If `K` is not 0,
  list them with `kubectl get quantonsparkapplications -A` and say plainly that running jobs
  among them will be killed.

Ask: "Remove all of the above?" One yes covers Steps 4 to 6. Deleting the namespace in Step 7
needs its own yes.

### Step 4: Uninstall the Helm release

Skip this step if Step 1 found no release. Otherwise run, with the name and namespace from
Step 1:

```bash
helm uninstall <release> -n <namespace>
```

If it fails, quote the error, run `helm list -A` again, and ask the user how to proceed. Do not
move to Step 5 until the release is gone or the user says to continue anyway.

### Step 5: Dry-run the cleanup script

Run:

```bash
test -x scripts/cleanup-secrets.sh || chmod +x scripts/cleanup-secrets.sh
./scripts/cleanup-secrets.sh
```

Show the `[dry-run] would delete ...` lines to the user. Compare them with Step 2. If the
script would delete something Step 2 did not show, stop and ask.

### Step 6: Delete

Run:

```bash
./scripts/cleanup-secrets.sh --confirm
```

Quote the script's final summary line.

### Step 7: Verify and offer namespace removal

Run the secret and CRD commands from Step 2 again. Success is: no secret lines, and
`CRD not found`.

If the CRD is still present, follow the `Terminating` entry in `docs/troubleshooting.md`. Show
the remaining objects and ask before touching any finalizer.

Then run `kubectl get all -n <namespace>` for the operator namespace. If it prints
`No resources found`, ask whether to delete the namespace. Only after a yes run
`kubectl delete namespace <namespace>`.

## Report

Fill every field from Step 7 output. Do not fill a field from memory of an earlier step.

```
Quanton Operator removal on context <ctx>
  Helm release:  <removed | was not installed | FAILED: <quoted error>>
  Secrets:       <N deleted across M namespaces | none found | still present: <list>>
  CRD:           <deleted | was not present | still present: <reason>>
  Namespace:     <deleted | kept | not empty | not applicable>
```

## Failure handling

- **`helm uninstall` fails.** Quote the error. Run `helm list -A`. Ask.
- **The script is not executable.** Step 5 already runs `chmod +x`. If that fails, quote the
  error.
- **`permission denied`, `forbidden`, or a CRD stuck in `Terminating`.** Read the matching
  entry in `docs/troubleshooting.md`. Do not patch finalizers without a yes.
