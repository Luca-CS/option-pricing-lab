// The builder (Structuring › Builder): a preset or a stored quote as the start, the sheet's form as the server draws
// it, the pricing options, and the quote as a job whose progress replaces the button. The start and the last quote are
// in the URL (`?preset=`, `?quote=`, `?job=`), so a link reopens the same view; the edits to the sheet and the pricing
// options are kept in this browser (v0's desk kept them too), until "Start again".
import { Accordion, Button, Group, SegmentedControl, Select, Stack, Switch, Text } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { ApiError, api, ok, type Schemas } from "../../api/client";
import { usePresets } from "../../api/queries";
import { store, stored } from "../../app/preferences";
import { SpacePage } from "../../app/Shell";
import { can, useMe } from "../../auth/session";
import { kindLabel } from "../../jobs/jobs";
import { useScreenJob } from "../../jobs/useScreenJob";
import { toRequest, type Values } from "../../lib/sheet";
import { JobProgress } from "../../ui/Flow";
import { Panel } from "../../ui/Panel";
import { Empty, Failure, Loading } from "../../ui/States";
import { commonClasses as c } from "../common";
import { BookDialog } from "./BookDialog";
import classes from "./Builder.module.css";
import { QuoteActions, QuoteView } from "./QuoteView";
import { SheetForm } from "./SheetForm";

type Sheet = { type: "autocall" | "bullet"; values: Values };
type Draft = { key: string; sheet: Sheet };
type Options = Schemas["PricingOptions"];

const DRAFT = "optlab-builder-draft";
const OPTIONS = "optlab-pricing-options";
const DEFAULT_OPTIONS: Options = { precision: "standard", greeks: true, mix_band: false };
const STICKY = [
  { value: "moneyness", label: "Sticky moneyness" },
  { value: "strike", label: "Sticky strike" },
  { value: "model", label: "The model's own" },
];

const PRECISIONS = [
  { value: "quick", label: "Quick" },
  { value: "standard", label: "Standard" },
  { value: "precise", label: "Precise" },
  { value: "max", label: "Max" },
];
const MODELS = [
  { value: "policy", label: "The desk's policy" },
  { value: "lv", label: "Local vol" },
  { value: "slv", label: "SLV" },
  { value: "heston", label: "Heston" },
  { value: "bs", label: "Black-Scholes" },
];

/** The surface's origin from a job's market (`origin` is a dict of each part's origin, or a sentence). */
function surfaceOrigin(origin: unknown): string | null {
  if (typeof origin === "string") return origin;
  if (origin && typeof origin === "object" && "surface" in origin)
    return String((origin as { surface: unknown }).surface);
  return null;
}

function useStartingSheet(preset: string | null, quote: string | null, trade: string | null): Sheet | null {
  const presets = usePresets();
  const booked = useQuery({
    queryKey: ["trading", "trade", trade],
    queryFn: () => ok(api.GET("/api/v1/trading/trades/{trade_id}", { params: { path: { trade_id: trade ?? "" } } })),
    enabled: Boolean(trade),
  });
  const saved = useQuery({
    queryKey: ["structuring", "quote", quote],
    queryFn: () =>
      ok(api.GET("/api/v1/structuring/quotes/{quote_id}", { params: { path: { quote_id: quote ?? "" } } })),
    enabled: Boolean(quote),
  });
  return useMemo(() => {
    if (trade) {
      const sheet = booked.data?.trade.product.term_sheet;
      return sheet ? toRequest(sheet as Record<string, unknown>) : null;
    }
    if (quote) return saved.data ? toRequest(saved.data.sheet as Record<string, unknown>) : null;
    const list = presets.data?.presets ?? [];
    const p = list.find((x) => x.name === preset) ?? list.find((x) => x.name === "phoenix") ?? list[0];
    return p ? toRequest(p.sheet) : null;
  }, [trade, booked.data, quote, saved.data, preset, presets.data]);
}

export default function Builder() {
  const [params, setParams] = useSearchParams();
  const preset = params.get("preset");
  const quoteParam = params.get("quote");
  const me = useMe().data;
  const presets = usePresets().data?.presets ?? [];
  const tradeParam = params.get("trade");
  const start = useStartingSheet(preset, quoteParam, tradeParam);
  // the sheet being edited is a draft of its start (a preset, a quote, a trade), kept in this browser: coming back to
  // the same start finds the edits where they were
  const startKey = tradeParam ? `trade:${tradeParam}` : quoteParam ? `quote:${quoteParam}` : `preset:${preset ?? ""}`;
  const [draft, setDraft] = useState<Draft | null>(null);
  useEffect(() => {
    if (!start) return;
    const kept = stored<Draft>(DRAFT);
    setDraft(kept?.key === startKey && kept.sheet ? kept : { key: startKey, sheet: start });
  }, [start, startKey]);
  useEffect(() => {
    if (draft) store(DRAFT, draft);
  }, [draft]);
  const sheet = draft?.key === startKey ? draft.sheet : null;
  const setSheet = (next: Sheet | ((s: Sheet) => Sheet)) =>
    setDraft((d) => (d ? { ...d, sheet: typeof next === "function" ? next(d.sheet) : next } : d));
  const edited = Boolean(sheet && start && JSON.stringify(sheet) !== JSON.stringify(start));
  const [debounced] = useDebouncedValue(sheet, 250);

  const form = useQuery({
    queryKey: ["structuring", "form", debounced],
    queryFn: async () => {
      try {
        const out = await ok(
          api.POST("/api/v1/structuring/termsheet/form", { body: debounced ?? { type: "autocall", values: {} } }),
        );
        return { out, problem: null as ApiError | null };
      } catch (e) {
        if (e instanceof ApiError && e.status === 422 && e.problem.form) return { out: null, problem: e };
        throw e;
      }
    },
    enabled: Boolean(debounced),
    placeholderData: keepPreviousData,
  });
  const problem = form.data?.problem ?? null;
  const shownForm = (form.data?.out?.form ?? problem?.problem.form) as Schemas["SheetForm"] | undefined;
  const errors = problem ? problem.fields() : {};
  const invalid = Boolean(problem) || form.isFetching || !form.data;

  const [options, keepOptions] = useState<Options>(() => ({ ...DEFAULT_OPTIONS, ...stored<Options>(OPTIONS) }));
  const setOptions = (o: Options) => {
    keepOptions(o);
    store(OPTIONS, o);
  };
  const job = useScreenJob("quote", `Quote of ${shownForm?.label ?? "a sheet"}`);
  const { jobId, tracked, running, envelope } = job;
  const quote = {
    mutate: () =>
      job.start.mutate(() =>
        ok(api.POST("/api/v1/structuring/quotes", { body: { sheet: sheet as Schemas["TermSheetIn"], options } })),
      ),
    isPending: job.start.isPending,
    error: job.start.error,
  };
  const result = envelope?.result;
  const [booking, setBooking] = useState(false);

  const set = (section: string, field: string, value: unknown) =>
    setSheet((s) => ({ ...s, values: { ...s.values, [section]: { ...(s.values[section] ?? {}), [field]: value } } }));

  return (
    <SpacePage>
      <Group gap="xs" wrap="wrap">
        {presets.map((p) => (
          <Button
            key={p.name}
            size="xs"
            radius="xl"
            variant={(preset ?? "phoenix") === p.name && !quoteParam && !tradeParam ? "light" : "default"}
            onClick={() => setParams({ preset: p.name })}
          >
            {p.label}
          </Button>
        ))}
        {quoteParam && (
          <Text size="sm" c="dimmed">
            from quote {quoteParam}
          </Text>
        )}
        {tradeParam && (
          <Text size="sm" c="dimmed">
            a copy of trade {tradeParam}
          </Text>
        )}
        {edited && (
          <Button size="xs" variant="subtle" onClick={() => start && setDraft({ key: startKey, sheet: start })}>
            Start again from {quoteParam ? "the quote" : tradeParam ? "the trade" : "the preset"}
          </Button>
        )}
      </Group>
      <div className={classes.layout}>
        <Panel
          title={shownForm?.label ?? "Term sheet"}
          hint="levels and rates in %, as typed; the server checks each field"
          flush
        >
          {!shownForm && form.isPending && (
            <div className={classes.pad}>
              <Loading lines={6} />
            </div>
          )}
          {form.error && <Failure error={form.error} />}
          {shownForm && sheet && (
            <>
              <div className={classes.mask}>
                <SegmentedControl
                  size="xs"
                  value={sheet.type}
                  onChange={(v) => setSheet({ ...sheet, type: v as Sheet["type"] })}
                  data={[
                    { value: "autocall", label: "Autocallable" },
                    { value: "bullet", label: "To maturity" },
                  ]}
                  aria-label="Product shape"
                />
              </div>
              <SheetForm form={shownForm} values={sheet.values} errors={errors} onChange={set} hidden={["costs"]} />
              <Accordion variant="filled" chevronPosition="left" className={classes.advanced}>
                <Accordion.Item value="costs">
                  <Accordion.Control>Desk's costs for this sheet</Accordion.Control>
                  <Accordion.Panel>
                    <Text size="xs" c="dimmed" mb={8}>
                      Empty fields take the cost table's value (Trading › Pricing policy).
                    </Text>
                    <SheetForm
                      form={{ ...shownForm, sections: shownForm.sections.filter((s) => s.name === "costs") }}
                      values={sheet.values}
                      errors={errors}
                      onChange={set}
                    />
                  </Accordion.Panel>
                </Accordion.Item>
                <Accordion.Item value="options">
                  <Accordion.Control>Pricing options</Accordion.Control>
                  <Accordion.Panel>
                    <Stack gap="sm">
                      <Select
                        label="Model"
                        data={MODELS}
                        value={options.model ?? "policy"}
                        onChange={(v) =>
                          setOptions({ ...options, model: v === "policy" ? null : (v as Options["model"]) })
                        }
                        allowDeselect={false}
                      />
                      <Switch
                        label="Control by the SLV gap (control variate)"
                        checked={options.control ?? true}
                        onChange={(e) => setOptions({ ...options, control: e.currentTarget.checked })}
                      />
                      <Switch
                        label="Mix band (SLV mix 0.25 to 0.75)"
                        checked={Boolean(options.mix_band)}
                        onChange={(e) => setOptions({ ...options, mix_band: e.currentTarget.checked })}
                      />
                      <Switch
                        label="Include the desk's costs"
                        checked={options.costs ?? true}
                        onChange={(e) => setOptions({ ...options, costs: e.currentTarget.checked })}
                      />
                      <Select
                        label="Delta convention"
                        data={STICKY}
                        value={options.sticky ?? "moneyness"}
                        onChange={(v) => setOptions({ ...options, sticky: (v ?? "moneyness") as Options["sticky"] })}
                        allowDeselect={false}
                      />
                      <Switch
                        label="Fast models (grid interpolation)"
                        checked={Boolean(options.fast)}
                        onChange={(e) => setOptions({ ...options, fast: e.currentTarget.checked })}
                      />
                      <Switch
                        label="Forward control variates"
                        checked={Boolean(options.control_forwards)}
                        onChange={(e) => setOptions({ ...options, control_forwards: e.currentTarget.checked })}
                      />
                      <Switch
                        label="SLV vega (recalibrates the leverage: slower)"
                        checked={Boolean(options.slv_vega)}
                        onChange={(e) => setOptions({ ...options, slv_vega: e.currentTarget.checked })}
                      />
                    </Stack>
                  </Accordion.Panel>
                </Accordion.Item>
              </Accordion>
              <div className={classes.go}>
                <SegmentedControl
                  size="xs"
                  value={options.precision ?? "standard"}
                  onChange={(v) => setOptions({ ...options, precision: v as Options["precision"] })}
                  data={PRECISIONS}
                  aria-label="Precision"
                />
                <Switch
                  size="xs"
                  label="Greeks"
                  checked={Boolean(options.greeks)}
                  onChange={(e) => setOptions({ ...options, greeks: e.currentTarget.checked })}
                />
                {running && tracked ? (
                  <JobProgress job={tracked} label="Quote" onCancel={() => void job.cancel()} />
                ) : can(me, "create_quote") ? (
                  <Button onClick={() => quote.mutate()} disabled={invalid} loading={quote.isPending}>
                    Quote
                  </Button>
                ) : (
                  <Text size="sm" c="dimmed">
                    Quoting needs the structurer role.
                  </Text>
                )}
              </div>
              {quote.error && <Failure error={quote.error} />}
              {problem && Object.keys(errors).length === 0 && <Failure error={problem} />}
            </>
          )}
        </Panel>
        <div className={classes.result}>
          {!jobId && (
            <Empty title="No quote yet">
              Pick a preset, change what you need, then Quote. The result stays here with the market and the method it
              used; Ctrl K finds it later.
            </Empty>
          )}
          {jobId && !result && job.failed && <Failure error={new Error(job.failed)} title="The quote failed" />}
          {jobId && !result && !job.failed && (
            <Panel title={kindLabel("quote")} hint="computing">
              <Loading lines={4} label="Quoting" />
            </Panel>
          )}
          {result && (
            <QuoteView
              q={result}
              quoteId={result.quote_id}
              asOf={envelope?.market?.as_of}
              origin={surfaceOrigin(envelope?.market?.origin)}
              actions={
                result.quote_id ? (
                  <QuoteActions
                    quoteId={result.quote_id}
                    onBook={can(me, "create_trade") ? () => setBooking(true) : undefined}
                  />
                ) : undefined
              }
            />
          )}
        </div>
      </div>
      {result?.quote_id && (
        <BookDialog opened={booking} onClose={() => setBooking(false)} quote={result} quoteId={result.quote_id} />
      )}
      <p className={c.faint}>
        The form, its visible fields and every check are the server's (POST /structuring/termsheet/form).
      </p>
    </SpacePage>
  );
}
