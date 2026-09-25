// This reproducer was run temporarily in the upstream utilization package.
// It is archived here so the filter-order claim can be reviewed without
// changing the upstream pull request.
package utilization

import (
    "context"
    "encoding/base64"
    "testing"
    "time"

    "github.com/go-logr/logr"
    fwksched "github.com/llm-d/llm-d-router/pkg/epp/framework/interface/scheduling"
    sessionaffinity "github.com/llm-d/llm-d-router/pkg/epp/framework/plugins/scheduling/filter/sessionaffinity"
)

func TestDiagnosticPinBeforeUtilizationKeepsOverloadedLocal(t *testing.T) {
    local := makeSchedulingEndpoint("local", 6, 0.1, time.Now())
    remote := makeSchedulingEndpoint("remote", 0, 0.1, time.Now())
    both := []fwksched.Endpoint{local, remote}
    detector := NewDetector("detector", Config{
        QueueDepthThreshold: 5,
        KVCacheUtilThreshold: 0.8,
        MetricsStalenessThreshold: time.Hour,
        Headroom: 0,
    }, logr.Discard())
    pin := sessionaffinity.NewSessionAffinity("pin", "x-benchmark-decoder", "")
    token := base64.StdEncoding.EncodeToString([]byte(local.GetMetadata().ID.String()))
    request := &fwksched.InferenceRequest{Headers: map[string]string{"x-benchmark-decoder": token}}

    oldOrder := pin.Filter(context.Background(), request, detector.Filter(context.Background(), request, both))
    if len(oldOrder) != 1 || oldOrder[0].GetMetadata().ID != remote.GetMetadata().ID {
        t.Fatalf("old order unexpectedly held local: %v", oldOrder)
    }
    newOrder := detector.Filter(context.Background(), request, pin.Filter(context.Background(), request, both))
    if len(newOrder) != 1 || newOrder[0].GetMetadata().ID != local.GetMetadata().ID {
        t.Fatalf("new order lost local: %v", newOrder)
    }
}
