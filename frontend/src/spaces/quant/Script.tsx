// The script lab (Quant › Script lab): a payoff written in the desk's script language, checked as it is typed (the
// first error at its line and column), then priced under a model on the market in use. A quote's script opens here
// (`?quote=`), and the reference sums up the grammar of optlab.structured.script.
import { Button, Group, NumberInput, Select, Text, Textarea } from "@mantine/core";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import { api, ok, type Schemas } from "../../api/client";
import { useMarketContext } from "../../api/queries";
import { SpacePage } from "../../app/Shell";
import { can, useMe } from "../../auth/session";
import { useScreenJob } from "../../jobs/useScreenJob";
import { formatNumber, formatSeconds } from "../../lib/format";
import { JobProgress } from "../../ui/Flow";
import { Kpi, KpiRow } from "../../ui/Kpi";
import { Panel } from "../../ui/Panel";
import { Failure } from "../../ui/States";
import { Est } from "../../ui/Value";
import { commonClasses as c } from "../common";
import classes from "./Quant.module.css";

// a starting payoff: a one-year barrier reverse convertible on the market in use, from its valuation date
function example(valuation: string): string {
  const start = valuation || "2021-06-30";
  const end = `${Number(start.slice(0, 4)) + 1}${start.slice(4)}`;
  return `underlying SPX
param N = 100
param K = 1.0
param B = 0.7

event ${start}
  S0 = SPX
end

event ${end} pay ${end}
  perf = SPX / S0
  if lowest(SPX, ${start}, ${end}) < B * S0 then
    pay N - N * max(K - perf, 0) as redemption
  else
    pay N + N * 0.05 as redemption
  end
end
`;
}

const MODELS = [
  { value: "lv", label: "Local vol (Dupire)" },
  { value: "slv", label: "SLV" },
  { value: "heston", label: "Heston" },
  { value: "bs", label: "Black-Scholes" },
];

const REFERENCE: [string, string][] = [
  ["underlying SPX", "a name the script reads (one per line)"],
  ["param B = 0.7", "a number set once"],
  ["event 2022-06-30 [pay 2022-07-07]", "a date, and when its payments settle; or t = 0.5 (years)"],
  ["x = expr", "a variable, kept for the next events"],
  ["pay expr as coupon", "a payment, labelled"],
  ["terminate", "the product ends here"],
  ["if … then … [else …] end", "a condition; and, or, not; < <= > >= =="],
  ["min, max, abs, exp, log, sqrt", "functions; worst, best of two or more names"],
  ["lowest(SPX, d1, d2)", "the lowest close between two dates; highest too"],
  ["variance(SPX, d1, d2[, lo, hi])", "realised variance, 252 days a year; in a corridor of levels lo to hi"],
];

type Model = Schemas["ScriptPriceIn"]["model"];

export default function Script() {
  const me = useMe().data;
  const [params] = useSearchParams();
  const context = useMarketContext();
  const quoteId = params.get("quote");
  const quote = useQuery({
    queryKey: ["structuring", "quote", quoteId],
    queryFn: () =>
      ok(api.GET("/api/v1/structuring/quotes/{quote_id}", { params: { path: { quote_id: quoteId ?? "" } } })),
    enabled: Boolean(quoteId),
  });
  const [script, setScript] = useState<string | null>(null);
  const [model, setModel] = useState<Model>("lv");
  const [paths, setPaths] = useState<number | string>(40_000);
  const valuation = context.data?.valuation ?? "";
  const fromQuote = quote.data?.result.product?.script ?? undefined;
  useEffect(() => {
    if (script === null && (fromQuote || (!quoteId && valuation))) setScript(fromQuote ?? example(valuation));
  }, [script, fromQuote, quoteId, valuation]);
  const text = script ?? "";
  const check = useMutation({
    mutationFn: (s: string) => ok(api.POST("/api/v1/structuring/script/check", { body: { script: s } })),
  });
  // checked as it is typed, once the typing pauses
  const checkNow = check.mutate;
  useEffect(() => {
    if (!text.trim()) return;
    const t = setTimeout(() => checkNow(text), 400);
    return () => clearTimeout(t);
  }, [text, checkNow]);
  const job = useScreenJob("script", "Script price", "job");
  const result = job.envelope?.result;
  const verdict = check.data;
  const lines = text.split("\n").length;
  const bad = verdict && !verdict.ok ? verdict.line : null;
  return (
    <SpacePage>
      <div className={c.wide}>
        <Panel title="The script" hint={verdict?.ok ? "checked" : verdict ? "an error" : "checking as you type"}>
          <div className={classes.editor}>
            <pre className={classes.gutter} aria-hidden="true">
              {Array.from({ length: lines }, (_, i) => (
                // biome-ignore lint/suspicious/noArrayIndexKey: a gutter line is its position
                <span key={i} className={i + 1 === bad ? classes.badLine : undefined}>
                  {i + 1}
                  {"\n"}
                </span>
              ))}
            </pre>
            <Textarea
              value={text}
              onChange={(e) => setScript(e.currentTarget.value)}
              autosize
              minRows={16}
              spellCheck={false}
              aria-label="Payoff script"
              classNames={{ input: classes.code, root: classes.codeRoot }}
            />
          </div>
          {verdict && !verdict.ok && (
            <Text size="sm" c="red" mt="sm" role="alert">
              Line {verdict.line}, column {verdict.col}: {verdict.message}
            </Text>
          )}
          {verdict?.ok && (
            <Text size="sm" c="dimmed" mt="sm">
              {(verdict.underlyings ?? []).join(", ")} · {verdict.events} events ·{" "}
              {Object.entries(verdict.params ?? {})
                .map(([k, v]) => `${k} = ${formatNumber(v, { digits: 2 })}`)
                .join(", ")}
            </Text>
          )}
          {check.error && <Failure error={check.error} title="Not checked" />}
        </Panel>
        <div className={c.stack}>
          <Panel title="Price it" hint="on the market in use, single-name scripts">
            {can(me, "script_price") ? (
              job.running && job.tracked ? (
                <JobProgress job={job.tracked} label="Script price" onCancel={() => void job.cancel()} />
              ) : (
                <Group gap="sm" align="flex-end" wrap="wrap">
                  <Select
                    label="Model"
                    size="sm"
                    w={190}
                    data={MODELS}
                    value={model}
                    onChange={(v) => setModel((v ?? "lv") as Model)}
                    allowDeselect={false}
                  />
                  <NumberInput
                    label="Paths"
                    size="sm"
                    w={120}
                    min={1000}
                    max={500_000}
                    step={10_000}
                    value={paths}
                    onChange={setPaths}
                  />
                  <Button
                    size="sm"
                    disabled={!verdict?.ok}
                    loading={job.start.isPending}
                    onClick={() =>
                      job.start.mutate(() =>
                        ok(
                          api.POST("/api/v1/structuring/script/price", {
                            body: { script: text, model, n_paths: typeof paths === "number" ? paths : 40_000 },
                          }),
                        ),
                      )
                    }
                  >
                    Price
                  </Button>
                </Group>
              )
            ) : (
              <Text size="sm" c="dimmed">
                Pricing a script takes the structurer's role.
              </Text>
            )}
            {job.failed && <Failure error={new Error(job.failed)} title="The price failed" />}
            {job.start.error && <Failure error={job.start.error} title="Not priced" />}
            {result && (
              <KpiRow label="The price">
                <Kpi
                  label="Price"
                  unit="per 100 of notional"
                  sub={`${result.model}, ${formatNumber(result.n_paths)} paths`}
                >
                  <Est value={result.price} />
                </Kpi>
                <Kpi label="Expected life" unit="years" sub={formatSeconds(result.seconds)}>
                  <span className={c.mono}>{formatNumber(result.expected_life, { digits: 2 })}</span>
                </Kpi>
              </KpiRow>
            )}
            {(result?.warnings ?? []).length > 0 && (
              <ul className={c.list}>
                {(result?.warnings ?? []).map((w) => (
                  <li key={String(w)} className={c.muted}>
                    {String(w)}
                  </li>
                ))}
              </ul>
            )}
          </Panel>
          <Panel title="The language" hint="one statement a line, blocks closed by end">
            <dl className={c.facts}>
              {REFERENCE.map(([code, what]) => (
                <div key={code} className={c.contents}>
                  <dt className={c.mono}>{code}</dt>
                  <dd className={classes.plain}>{what}</dd>
                </div>
              ))}
            </dl>
          </Panel>
        </div>
      </div>
    </SpacePage>
  );
}
