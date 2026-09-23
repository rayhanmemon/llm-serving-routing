package topologyaffinity

import (
 "context"
 "encoding/json"
 "os"
 "reflect"
 "sort"
 "testing"

 fwk "github.com/llm-d/llm-d-router/pkg/epp/framework/interface/plugin"
 sched "github.com/llm-d/llm-d-router/pkg/epp/framework/interface/scheduling"
 concurrency "github.com/llm-d/llm-d-router/pkg/epp/framework/plugins/datalayer/attribute/concurrency"
 topology "github.com/llm-d/llm-d-router/pkg/epp/framework/plugins/datalayer/attribute/topology"
 "github.com/llm-d/llm-d-router/pkg/epp/framework/plugins/scheduling/filter/utilization"
 "github.com/llm-d/llm-d-router/pkg/epp/framework/plugins/scheduling/scorer/activerequest"
 affinity "github.com/llm-d/llm-d-router/pkg/epp/framework/plugins/scheduling/scorer/topologyaffinity"
)
func TestPreparedPolicyParity(t *testing.T) {
 var cases []struct {Policy string; Params map[string]float64; Local,Remote int64; Expected []string}
 raw,err:=os.ReadFile("/proof/cases.json");if err!=nil{t.Fatal(err)}
 if err=json.Unmarshal(raw,&cases);err!=nil{t.Fatal(err)}
 ctx:=context.Background();req:=requestWithPeer(&topology.Topology{Hostname:"h1"})
 for i,c:=range cases {
  local:=makeEndpoint(t,"local",&topology.Topology{Hostname:"h1"});remote:=makeEndpoint(t,"remote",&topology.Topology{Hostname:"h2"})
  local.Put(concurrency.InFlightLoadDataKey,&concurrency.InFlightLoad{Requests:c.Local});remote.Put(concurrency.InFlightLoadDataKey,&concurrency.InFlightLoad{Requests:c.Remote})
  endpoints:=[]sched.Endpoint{local,remote}
  if c.Policy=="absolute-cap" {
   raw,_:=json.Marshal(map[string]any{"conditions":[]map[string]any{{"metric":"active-requests","maxValue":c.Params["cap"]}},"fallbackOnEmpty":true})
   f,e:=utilization.Factory("cap",fwk.StrictDecoder(raw),nil);if e!=nil{t.Fatal(e)}
   endpoints=f.(sched.Filter).Filter(ctx,req,endpoints)
  }
  if c.Policy=="hard" || c.Policy=="absolute-cap" || c.Policy=="allowance" {
   params:=map[string]any{"minAffinity":"host"};if c.Policy=="allowance"{params["loadAllowance"]=c.Params["allowance"]}
   raw,_:=json.Marshal(params);f,e:=Factory("topology",fwk.StrictDecoder(raw),nil);if e!=nil{t.Fatal(e)}
   endpoints=f.(sched.Filter).Filter(ctx,req,endpoints)
  }
  busy:=1.0;if v,ok:=c.Params["max_busy_score"];ok{busy=v}
  scorer:=activerequest.NewActiveRequest(ctx,&activerequest.Parameters{IdleThreshold:int(c.Params["idle_threshold"]),MaxBusyScore:&busy})
  scores:=scorer.Score(ctx,req,endpoints)
  if c.Policy=="soft"{
   sc,e:=affinity.Factory("affinity",nil,nil);if e!=nil{t.Fatal(e)}
   for ep,score:=range sc.(sched.Scorer).Score(ctx,req,endpoints){scores[ep]+=c.Params["weight"]*score}
  }
  maximum:=-1.0;got:=[]string{}
  for _,ep:=range endpoints {score:=scores[ep];if score>maximum{maximum=score;got=[]string{ep.GetMetadata().ID.Name}}else if score==maximum{got=append(got,ep.GetMetadata().ID.Name)}}
  sort.Strings(got);sort.Strings(c.Expected)
  if !reflect.DeepEqual(got,c.Expected){t.Fatalf("case%d policy%s local%d remote%d params%v: Go%v tuner%v",i,c.Policy,c.Local,c.Remote,c.Params,got,c.Expected)}
 }
 t.Logf("Verified %d cases against actual Go filters/scorers; ties compare candidate sets",len(cases))
}
