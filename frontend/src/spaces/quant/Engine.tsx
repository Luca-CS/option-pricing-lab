// The engine (Quant › Engine): where Monte Carlo paths come from on this machine: the compiled engine and its CUDA
// build, the devices it sees, the CPU threads, and which engine the desk resolved to. The numbers and the method
// of the measured ablation are in the repository (reports/, packages/native/cuda).
import { Badge, Text } from "@mantine/core";
import { useEngine } from "../../api/queries";
import { SpacePage } from "../../app/Shell";
import { formatNumber } from "../../lib/format";
import { Kpi, KpiRow } from "../../ui/Kpi";
import { Panel } from "../../ui/Panel";
import { Empty, Failure, Loading } from "../../ui/States";
import { commonClasses as c, DataTable } from "../common";

const ENGINES: [string, string][] = [
  ["numpy", "the library's own pricers, bit for bit the numbers of its tests"],
  ["cpu", "the compiled engine on the CPU, in double, on every thread"],
  ["gpu", "the CUDA engine in float, with Kahan sums; Philox draws, so a price depends only on the seed and the paths"],
  ["auto", "the GPU when there is one, else the CPU engine, else NumPy"],
];

export default function Engine() {
  const engine = useEngine();
  const e = engine.data;
  return (
    <SpacePage>
      {engine.isPending && <Loading />}
      {engine.error && <Failure error={engine.error} />}
      {e && (
        <>
          <KpiRow label="The engine">
            <Kpi label="In use" sub={`asked: ${e.engine}`}>
              <span className={c.mono}>{e.resolved}</span>
            </Kpi>
            <Kpi
              label="Compiled engine"
              sub={e.compiled ? "optlab.native._ext is loaded" : "not built on this machine"}
            >
              <span className={c.mono}>{e.compiled ? "yes" : "no"}</span>
            </Kpi>
            <Kpi label="CUDA build" sub={e.cuda_build ? "the GPU kernels are in it" : "CPU engines only"}>
              <span className={c.mono}>{e.cuda_build ? "yes" : "no"}</span>
            </Kpi>
            <Kpi label="CPU threads" sub="for the CPU engine and the evaluation of batches">
              <span className={c.mono}>{e.cpu_threads ?? "—"}</span>
            </Kpi>
          </KpiRow>
          <div className={c.grid}>
            <Panel title="Devices" hint="as the CUDA runtime reports them" flush>
              {(e.devices ?? []).length === 0 ? (
                <Empty title="No CUDA device">The desk computes on the CPU.</Empty>
              ) : (
                <DataTable
                  caption="The CUDA devices of this machine"
                  head={["Device", "Compute capability", "SMs", "Clock, MHz", "Memory, GB", "Free, GB", ""]}
                  num={[2, 3, 4, 5]}
                  rows={(e.devices ?? []).map((d) => ({
                    key: d.name,
                    cells: [
                      d.name,
                      <span key="cc" className={c.mono}>
                        sm_{(d.compute_capability ?? "").replace(".", "")}
                      </span>,
                      d.sm_count ?? "—",
                      formatNumber(d.clock_mhz),
                      formatNumber(d.memory_gb, { digits: 1 }),
                      formatNumber(d.free_gb, { digits: 1 }),
                      <span key="w">
                        {d.watchdog && (
                          <Badge size="xs" variant="outline" title="drives a display: long kernels are cut by the OS">
                            display watchdog
                          </Badge>
                        )}
                      </span>,
                    ],
                  }))}
                />
              )}
            </Panel>
            <Panel title="The engines" hint="engine= on a run, or OPTLAB_ENGINE for the process">
              <dl className={c.facts}>
                {ENGINES.map(([name, what]) => (
                  <div key={name} className={c.contents}>
                    <dt className={c.mono}>{name}</dt>
                    <dd className={c.muted}>{what}</dd>
                  </div>
                ))}
              </dl>
              <Text size="sm" c="dimmed" mt="md">
                A job that falls back from the engine to NumPy says why in its timings (engine_note).
              </Text>
            </Panel>
          </div>
        </>
      )}
    </SpacePage>
  );
}
