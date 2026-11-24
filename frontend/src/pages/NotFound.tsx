import { Anchor } from "@mantine/core";
import { Link } from "react-router";
import { SpacePage } from "../app/Shell";
import { homeOf } from "../app/spaces";
import { useMe } from "../auth/session";
import { Empty } from "../ui/States";

export function NotFound() {
  const me = useMe().data;
  return (
    <SpacePage title="Not here">
      <Empty
        title="Nothing at this address"
        action={
          <Anchor component={Link} to={homeOf(me)}>
            Go home
          </Anchor>
        }
      >
        The page does not exist, or it belongs to a role this account does not hold. Ctrl K finds a trade, a quote or a
        screen.
      </Empty>
    </SpacePage>
  );
}
