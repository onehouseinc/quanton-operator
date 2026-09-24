---
name: clean-uninstall
description: Remove the Quanton Operator Helm release, the secrets it replicated into job namespaces, and its CRD from a Kubernetes cluster. Use whenever the user wants to uninstall, remove, tear down, reset, or clean up the quanton-operator, or reports leftover quanton-operator secrets or CRDs after a helm uninstall.
compatibility: Requires kubectl and helm on PATH, a kubeconfig context that points at the target cluster, and the repository checked out so that scripts/cleanup-secrets.sh is available. Deleting the CRD needs cluster-admin rights.
allowed-tools: Bash, Read, AskUserQuestion
metadata:
  version: "2"
---

# Clean uninstall of the Quanton Operator

`helm uninstall` removes the chart's own resources and leaves two things behind: the secrets
the operator copies into every job namespace, and the `QuantonSparkApplication` CRD, which Helm
never deletes by design. This skill removes all three in a fixed order, with an inventory, a
dry run, and a confirmation before anything is deleted.

## How to read this skill

This skill is written for any agent runtime. It names actions, not tools. Map them like this:

| Action | What to do in your runtime |
|---|---|
| **Run** | Execute the command with your shell tool. Read the whole output before you continue. |
| **Read** | Open the file with your file-read tool. |
| **Ask** | Put the question to the user and end your turn. Use a structured-choice tool if you have one, otherwise plain text. |
| **Wait** | Use the bounded loop the step gives you. Set its bound below your runtime's command timeout. Never sleep or poll in your own turns. |

## Ground rules

1. **Report only what a command printed.** Quote the line that supports each claim. If you did not see a value, say so.
2. **One check per claim.** "Installed", "Running", "Completed", and "PASS" each need command output that shows the word.
3. **The fact table below is a plan, not the truth.** If a command contradicts it, trust the command, tell the user, and stop if the difference matters.
4. **Every wait is bounded.** Never run a command that can block without a timeout. Do not use `kubectl logs -f`.
5. **Ask before destructive or costly actions.** Deleting, overwriting, and submitting work to a paid cluster each need a fresh yes. A yes covers one action.
6. **Never print secrets.** This includes `onehouse-values.yaml`, Kubernetes Secret data, and API keys.
7. **Separate environment problems from engine problems**, and name the evidence for the split.
8. **Do not guess names, phases, or numbers.** Get pod names from `kubectl get pods`. Get counts from `grep -c`.

## Facts this skill relies on

| Item | Value |
|---|---|
| Helm chart | `quanton-operator`. The release name and namespace are usually the same, but discover them; do not assume. |
| Replicated secrets | `quanton-operator-cert`, `quanton-operator-docker-secret`, `quanton-operator-mtls-secret` |
| CRD | `quantonsparkapplications.quantonsparkoperator.onehouse.ai` |
| Cleanup script | `scripts/cleanup-secrets.sh`. Dry run by default. `--confirm` deletes. `--namespace <ns>` narrows to one namespace. |
| Side effect of CRD deletion | Kubernetes deletes every `QuantonSparkApplication` object in the cluster, including running jobs. |

## Procedure

### Step 1: Confirm the target cluster

Run:

```bash
kubectl config current-context
kubectl get nodes --no-headers 2>&1 | head -3
```

Show the context name to the user. Ask: "This removes the Quanton Operator from the cluster in
context `<name>`. Is that the cluster you mean?" Continue only on a clear yes. Uninstalling from
the wrong cluster is the worst outcome this skill can produce.

### Step 2: Check tools

Run:

```bash
command -v kubectl helm
```

If a line is missing, name the missing tool and stop.

### Step 3: Inventory what exists

Run all four commands. Empty output is a valid answer, not an error.

```bash
helm list -A -o json | python3 -c 'import json,sys; rows=[r for r in json.load(sys.stdin) if "quanton-operator" in r["chart"]]; print("\n".join(f"{r[\"name\"]} {r[\"namespace\"]} {r[\"chart\"]}" for r in rows) or "no quanton-operator release")'
for s in quanton-operator-cert quanton-operator-docker-secret quanton-operator-mtls-secret; do
  kubectl get secrets -A --field-selector "metadata.name=$s" --no-headers 2>/dev/null
done
kubectl get crd quantonsparkapplications.quantonsparkoperator.onehouse.ai --no-headers 2>/dev/null || echo "CRD not found"
kubectl get quantonsparkapplications -A --no-headers 2>/dev/null | wc -l
```

Record the release name and namespace, the list of secrets with their namespaces, whether the
CRD exists, and the count of `QuantonSparkApplication` objects.

If there is no release, no secret, and no CRD, tell the user there is nothing to clean up and
stop.

### Step 4: Show the plan and ask once

Present the inventory as a list, using the values from Step 3:

- Helm release `<name>` in namespace `<ns>`, or "none".
- `<N>` secrets across `<M>` namespaces, or "none".
- CRD present or absent.
- `<K>` `QuantonSparkApplication` objects that CRD deletion will also delete. If `K` is not 0,
  list them with `kubectl get quantonsparkapplications -A` and say plainly that running jobs
  among them will be killed.

Ask: "Remove all of the above?" One yes covers Steps 5 to 7. Deleting the namespace in Step 8
needs its own yes.

### Step 5: Uninstall the Helm release

Skip this step if Step 3 found no release. Otherwise run, with the name and namespace from
Step 3:

```bash
helm uninstall <release> -n <namespace>
```

If it fails, quote the error, run `helm list -A` again, and ask the user how to proceed. Do not
move to Step 6 until the release is gone or the user says to continue anyway.

### Step 6: Dry-run the cleanup script

Run:

```bash
test -x scripts/cleanup-secrets.sh || chmod +x scripts/cleanup-secrets.sh
./scripts/cleanup-secrets.sh
```

Show the `[dry-run] would delete ...` lines to the user. Compare them with Step 3. If the
script would delete something Step 3 did not show, stop and ask.

### Step 7: Delete

Run:

```bash
./scripts/cleanup-secrets.sh --confirm
```

Quote the script's final summary line.

### Step 8: Verify and offer namespace removal

Run the secret and CRD commands from Step 3 again. Success is: no secret lines, and
`CRD not found`.

If the CRD is still present, run
`kubectl get crd quantonsparkapplications.quantonsparkoperator.onehouse.ai -o jsonpath='{.metadata.deletionTimestamp}'`.
A timestamp means the CRD is stuck in Terminating, usually because a `QuantonSparkApplication`
still carries a finalizer that the removed operator can no longer clear. Show the objects and
ask before touching any finalizer.

Then run `kubectl get all -n <namespace>` for the operator namespace. If it prints
`No resources found`, ask whether to delete the namespace. Only after a yes run
`kubectl delete namespace <namespace>`.

## Report

Fill every field from Step 8 output. Do not fill a field from memory of an earlier step.

```
Quanton Operator removal on context <ctx>
  Helm release:  <removed | was not installed | FAILED: <quoted error>>
  Secrets:       <N deleted across M namespaces | none found | still present: <list>>
  CRD:           <deleted | was not present | still present: <reason>>
  Namespace:     <deleted | kept | not empty | not applicable>
```

## Failure handling

- **`helm uninstall` fails.** Quote the error. Run `helm list -A`. Ask.
- **`permission denied` or `forbidden`.** Quote the error and stop. CRD deletion needs
  cluster-admin rights. This is an access problem, not an operator problem.
- **The script is not executable.** Step 6 already runs `chmod +x`. If that fails, quote the
  error.
- **The CRD stays in Terminating.** See Step 8. Do not patch finalizers without a yes.
