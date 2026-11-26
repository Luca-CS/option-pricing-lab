// Booking a quote (the bridge quote → trade, docs/ux.md §3): the trade takes the quote's sheet and its
// solved coupon, and the tag `quote:<id>` that freezes the quote's desk lines as the trade's reserve.
import { Button, Group, Modal, Stack, Text, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router";
import { api, ok, type Schemas } from "../../api/client";
import { formatPercent } from "../../lib/format";
import { toRequest } from "../../lib/sheet";
import { type DeskSide, pricedSide, signedQuantity } from "../../lib/side";
import { Failure } from "../../ui/States";
import { SideSize } from "../common";
import type { QuoteResult } from "./QuoteView";

export function BookDialog({
  opened,
  onClose,
  quote,
  quoteId,
}: {
  opened: boolean;
  onClose: () => void;
  quote: QuoteResult;
  quoteId: string;
}) {
  const sheet = quote.sheet as Record<string, unknown>;
  const [side, setSide] = useState<DeskSide>(pricedSide(sheet));
  const [size, setSize] = useState<number | string>(1);
  const [counterparty, setCounterparty] = useState("");
  const [book, setBook] = useState("default");
  const queries = useQueryClient();
  const navigate = useNavigate();
  // what the quote solved goes into the trade: a coupon, or a swap's strike (the server writes it into the sheet)
  const solvedKind = quote.headline.kind === "coupon" || quote.headline.kind === "strike";
  const coupon = solvedKind ? (quote.coupon_rate?.value ?? null) : null;
  const create = useMutation({
    mutationFn: () => {
      const body: Schemas["TradeIn"] = {
        sheet: toRequest(sheet),
        quantity: signedQuantity(side, Number(size)),
        coupon_rate: coupon,
        counterparty,
        book,
        tags: [`quote:${quoteId}`],
      };
      return ok(api.POST("/api/v1/trading/trades", { body }));
    },
    onSuccess: (trade) => {
      const id = trade.trade_id;
      void queries.invalidateQueries({ queryKey: ["trading"] });
      notifications.show({
        color: "accent",
        title: `Booked as ${id}`,
        message: "It is in the book, with its reserve.",
      });
      onClose();
      navigate(`/trading/trades/${encodeURIComponent(id)}`);
    },
  });
  return (
    <Modal opened={opened} onClose={onClose} title="Book this quote" size="md">
      <Stack gap="sm">
        <Text size="sm" c="dimmed">
          The trade takes the quote's sheet{coupon !== null ? ` and its coupon, ${formatPercent(coupon)} p.a.` : ""}.
          The quote's desk lines are frozen as its reserve.
        </Text>
        <SideSize side={side} onSide={setSide} size={size} onSize={setSize} />
        {side !== pricedSide(sheet) && (
          <Text size="sm" c="amber">
            The quote was priced for the desk {pricedSide(sheet) === "sold" ? "selling" : "buying"}: the trade will
            carry a side warning.
          </Text>
        )}
        <TextInput label="Counterparty" value={counterparty} onChange={(e) => setCounterparty(e.currentTarget.value)} />
        <TextInput label="Book" value={book} onChange={(e) => setBook(e.currentTarget.value)} />
        {create.error && <Failure error={create.error} title="Not booked" />}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button
            color="amber"
            loading={create.isPending}
            onClick={() => create.mutate()}
            disabled={!(Number(size) > 0)}
          >
            Book
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
