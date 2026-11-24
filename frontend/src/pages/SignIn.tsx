import { Alert, Button, PasswordInput, TextInput } from "@mantine/core";
import { type FormEvent, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router";
import { ApiError } from "../api/client";
import { useMe, useSignIn } from "../auth/session";
import classes from "./SignIn.module.css";

export function SignIn() {
  const me = useMe();
  const signIn = useSignIn();
  const navigate = useNavigate();
  const location = useLocation();
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const from = (location.state as { from?: string } | null)?.from ?? "/";

  if (me.data) {
    return <Navigate to={from} replace />;
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    signIn.mutate({ name, password }, { onSuccess: () => navigate(from, { replace: true }) });
  }

  const error =
    signIn.error instanceof ApiError ? signIn.error.message : signIn.error ? "The desk cannot be reached." : null;

  return (
    <main className={classes.page}>
      <form className={classes.panel} onSubmit={submit} aria-labelledby="sign-in-title">
        <div className={classes.brand}>
          <span className={classes.mark} aria-hidden="true">
            o
          </span>
          <span>optlab desk</span>
        </div>
        <h1 id="sign-in-title" className={classes.title}>
          Sign in
        </h1>
        <TextInput
          label="Name"
          autoComplete="username"
          value={name}
          onChange={(e) => setName(e.currentTarget.value)}
          required
          data-autofocus
        />
        <PasswordInput
          label="Password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.currentTarget.value)}
          required
        />
        {error && (
          <Alert color="red" variant="light" role="alert">
            {error}
          </Alert>
        )}
        <Button type="submit" loading={signIn.isPending} fullWidth>
          Sign in
        </Button>
        <p className={classes.note}>
          Accounts are made by an admin, or on the desk's machine with <code>optlab users create</code>.
        </p>
      </form>
    </main>
  );
}
