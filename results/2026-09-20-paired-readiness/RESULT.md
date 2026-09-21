# Guard readiness interrupted before GPU allocation

After Rayhan granted temporary project-scoped administrator access, the cleanup service account/group/permit and CPU node were created successfully. The controller then aborted on a20-second timeout reading the guard's readiness file through `kubectl exec`. Its `finally` block began teardown. **No GPU node was allocated and no inference ran.**

During cleanup, the guard log showed package-installation DNS failures and CoreDNS Pods were being drained. Those observations do not establish DNS as the original cause of the readiness timeout. The immediate failure was the controller treating that read timeout as fatal.

Corrections: read the guard's explicit armed acknowledgement from its logs; retry bounded read-only status/readiness timeouts; use the host's DNS for this host-network guard, which does not need Kubernetes service DNS. Mutations are not replayed. A controller rehearsal now injects the readiness timeout and verifies later acknowledgement before GPU allocation. All182 local tests pass. Live guard qualification remains required.

All paid resources and Terraform state were verified empty. CPU compute plus conservatively estimated disk cost was **$0.04122 before tax**, not invoice-verified. **$48.79 remains**. Source correction: `4e381b5`. Temporary user-granted admin access is retained only for the corrected attempt and must be revoked after final experiment cleanup.
