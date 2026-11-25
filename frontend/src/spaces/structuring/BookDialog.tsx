// Booking a quote (the bridge quote → trade, docs/ux.md §3): the trade takes the quote's sheet and its
// solved coupon, and the tag `quote:<id>` that freezes the quote's desk lines as the trade's reserve.
import { Button, Group, Modal, NumberInput, Stack, Text, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router";
import { api, ok, type Schemas } from "../../api/client";
import { formatPercent } from "../../lib/format";
import { toRequest } from "../../lib/sheet";
import { Failure } from "../../ui/States";
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
  const side = ((sheet.costs as Record<string, unknown> | undefined)?.side ?? "sell") as string;
  const [quantity, setQuantity] = useState<number | string>(side === "sell" ? -1 : 1);
  const [counterparty, setCounterparty] = useState("");
  const [book, setBook] = useState("default");
  const queries = useQueryClient();
  const navigate = useNavigate();
  const coupon = quote.headline.kind === "coupon" ? (quote.coupon_rate?.value ?? null) : null;
  const create = useMutation({
    mutationFn: () => {
      const body: Schemas["TradeIn"] = {
        sheet: toRequest(sheet),
        quantity: Number(quantity),
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
        <NumberInput
          label="Quantity"
          description="in notionals; negative when the desk sold the note (the book holds the issuer's side)"
          value={quantity}
          onChange={setQuantity}
          step={0.5}
          decimalScale={4}
        />
        <TextInput label="Counterparty" value={counterparty} onChange={(e) => setCounterparty(e.currentTarget.value)} />
        <TextInput label="Book" value={book} onChange={(e) => setBook(e.currentTarget.value)} />
        {create.error && <Failure error={create.error} title="Not booked" />}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button color="amber" loading={create.isPending} onClick={() => create.mutate()} disabled={!Number(quantity)}>
            Book
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
