---
name: clean-uninstall
description: Use when the user wants to uninstall, remove, or clean up the quanton-operator Helm chart and its leftover secrets from a Kubernetes cluster
allowed-tools: Bash, Read, Glob, Grep, AskUserQuestion
---

# Clean Uninstall Quanton Operator

Fully removes the Quanton Operator Helm release and cleans up secrets it created across namespaces.

## Why This Exists

`helm uninstall` removes the chart resources but leaves behind secrets the operator replicated to job namespaces:
- `quanton-operator-cert`
- `quanton-operator-docker-secret`
- `quanton-operator-mtls-secret`

The cleanup script at `scripts/cleanup-secrets.sh` handles finding and deleting these.

## Execution Flow

### Step 1: Verify prerequisites

Check that `kubectl` and `helm` are available. Stop if either is missing.

### Step 2: Show current state

Run these and show the user what's installed:
```bash
helm list -A | grep quanton-operator
kubectl get secrets -A | grep quanton-operator
```

If neither the Helm release nor any secrets are found, tell the user there's nothing to clean up and stop.

### Step 3: Confirm with user

Show what will be removed (Helm release and/or secrets) and ask the user to confirm before proceeding.

### Step 4: Uninstall Helm release

If the Helm release exists, uninstall it:
```bash
helm uninstall quanton-operator -n quanton-operator
```

Wait for completion. If the namespace `quanton-operator` is now empty, ask the user if they'd like to delete it too.

### Step 5: Clean up secrets

Run the cleanup script in dry-run mode first to show what will be deleted:
```bash
./scripts/cleanup-secrets.sh
```

Then run with `--confirm`:
```bash
./scripts/cleanup-secrets.sh --confirm
```

### Step 6: Verify

Run `kubectl get secrets -A | grep quanton-operator` to confirm everything is gone. Report the result to the user.

## Error Handling

- If `helm uninstall` fails, show the error and check if the release even exists (`helm list -A`).
- If the cleanup script isn't executable, run `chmod +x scripts/cleanup-secrets.sh` first.
- Never expose credentials or secret values in output.
