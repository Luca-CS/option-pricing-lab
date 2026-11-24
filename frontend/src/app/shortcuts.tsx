// The keyboard (kept from the v0 desk): `g` then a letter goes to a space, `g h` home, `?` lists the keys, `t` swaps
// the theme. Keys typed in a field are the field's.
import { Kbd, Modal, Table, useMantineColorScheme } from "@mantine/core";
import { useEffect } from "react";
import { useNavigate } from "react-router";
import { useMe } from "../auth/session";
import { homeOf, SPACES } from "./spaces";

function typing(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el) return false;
  return el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName);
}

export function useShortcuts({ onHelp }: { onHelp: () => void }) {
  const navigate = useNavigate();
  const me = useMe().data;
  const { toggleColorScheme } = useMantineColorScheme();
  useEffect(() => {
    let pending = 0;
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey || typing(e.target)) return;
      const key = e.key.toLowerCase();
      if (pending && Date.now() - pending < 1500) {
        pending = 0;
        const space = SPACES.find((s) => s.key === key);
        if (key === "h") navigate(homeOf(me));
        else if (space) navigate(`/${space.id}`);
        return;
      }
      if (key === "g") pending = Date.now();
      else if (e.key === "?") onHelp();
      else if (key === "t") toggleColorScheme();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate, me, onHelp, toggleColorScheme]);
}

export function ShortcutsHelp({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const rows: [string[], string][] = [
    [["Ctrl", "K"], "search a trade, a quote, a name or a screen"],
    [["g", "h"], "home"],
    ...SPACES.map((s): [string[], string] => [["g", s.key], s.label]),
    [["t"], "swap graphite and paper"],
    [["?"], "this list"],
  ];
  return (
    <Modal opened={opened} onClose={onClose} title="Keyboard" size="sm">
      <Table withRowBorders={false} verticalSpacing={4}>
        <Table.Tbody>
          {rows.map(([keys, what]) => (
            <Table.Tr key={what}>
              <Table.Td>
                {keys.map((k, i) => (
                  <span key={k}>
                    {i > 0 && " then "}
                    <Kbd>{k}</Kbd>
                  </span>
                ))}
              </Table.Td>
              <Table.Td>{what}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Modal>
  );
}
