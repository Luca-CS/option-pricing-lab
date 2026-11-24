import { Button } from "@mantine/core";
import { useNavigate } from "react-router";
import { useMe, useSignOut } from "../auth/session";
import classes from "./Home.module.css";

// The F1 home: who is signed in. The shell (rail, context bar, role homes) replaces it at F3.
export function Home() {
  const me = useMe().data;
  const signOut = useSignOut();
  const navigate = useNavigate();

  return (
    <div className={classes.page}>
      <header className={classes.bar}>
        <span className={classes.mark} aria-hidden="true">
          o
        </span>
        <span>optlab desk</span>
        <span className={classes.spacer} />
        <span className={classes.who}>
          {me?.name} <span className={classes.role}>{me?.roles.join(", ")}</span>
        </span>
        <Button
          variant="default"
          size="xs"
          loading={signOut.isPending}
          onClick={() => signOut.mutate(undefined, { onSettled: () => navigate("/sign-in", { replace: true }) })}
        >
          Sign out
        </Button>
      </header>
      <main className={classes.main}>
        <h1 className={classes.title}>Signed in as {me?.name}</h1>
        <p className={classes.muted}>
          {me?.permissions.length} operations of the API are open to this account. The desk's spaces come next.
        </p>
      </main>
    </div>
  );
}
