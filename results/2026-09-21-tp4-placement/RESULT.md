# H200 placement rejected before engine deployment

Nebius's native instance-create operation returned `NotEnoughResources` after a scheduling timeout for the local eight-H200 preemptible (spot) VM. No engine container or model request ran, and the remote host was never requested. The agent interrupted the waiting Terraform apply after observing the native failure, then verified full cleanup.

The conservative estimate is **$2.68 before tax**. It includes the failed GPU scheduling interval and may exceed the actual provider charge; it is not an invoice total. The capacity-advice API still reported high preemptible availability, but explicitly marked that data stale, with an effective time several hours earlier. Advice was not treated as a reservation or proof that allocation would succeed.

The model configuration remained the corrected Qwen3-32B TP4 setup. A subsequent bounded attempt was made within the same $100 logical-session cap. This failure establishes a capacity blocker at that time, not a model, networking or router-performance result.
