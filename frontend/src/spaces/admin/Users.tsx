// Users (Admin › Users): the accounts, their roles and state. An admin adds one, changes its roles, disables it or
// sets a new password; each change ends the user's sessions and is audited (the server does both).
import { Badge, Button, Group, Modal, MultiSelect, PasswordInput, Stack, Text, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, ok, type Schemas } from "../../api/client";
import { SpacePage } from "../../app/Shell";
import { useMe } from "../../auth/session";
import { formatDate } from "../../lib/format";
import { Panel } from "../../ui/Panel";
import { Failure, Loading } from "../../ui/States";
import { commonClasses as c, DataTable } from "../common";

type User = Schemas["UserOut"];
type Role = NonNullable<Schemas["UserUpdate"]["roles"]>[number];

// the roles the server knows (the type checks each name against the document)
const ROLES = ["trader", "structurer", "risk", "quant", "viewer", "admin"] satisfies Role[];

/** A long random password, for the admin to hand over once (never stored by the front). */
export function generatePassword(length = 20): string {
  const alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789-_";
  const bytes = crypto.getRandomValues(new Uint8Array(length));
  return Array.from(bytes, (b) => alphabet[b % alphabet.length]).join("");
}

function PasswordField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <Group gap="xs" align="flex-end" wrap="nowrap">
      <PasswordInput
        label="Password"
        description="at least 12 characters; hand it over yourself"
        value={value}
        onChange={(e) => onChange(e.currentTarget.value)}
        style={{ flex: 1 }}
        autoComplete="new-password"
      />
      <Button variant="default" size="sm" onClick={() => onChange(generatePassword())}>
        Generate
      </Button>
    </Group>
  );
}

function AddUser({ onClose }: { onClose: () => void }) {
  const queries = useQueryClient();
  const [name, setName] = useState("");
  const [roles, setRoles] = useState<string[]>(["viewer"]);
  const [password, setPassword] = useState("");
  const add = useMutation({
    mutationFn: () => ok(api.POST("/api/v1/admin/users", { body: { name, password, roles: roles as Role[] } })),
    onSuccess: (u) => {
      void queries.invalidateQueries({ queryKey: ["admin", "users"] });
      notifications.show({ color: "accent", title: `${u.name} added`, message: u.roles.join(", ") });
      onClose();
    },
  });
  return (
    <Stack gap="sm">
      <TextInput
        label="Name"
        description="lower case, 2 to 32 characters"
        value={name}
        onChange={(e) => setName(e.currentTarget.value)}
        autoComplete="off"
      />
      <MultiSelect label="Roles" data={ROLES} value={roles} onChange={setRoles} />
      <PasswordField value={password} onChange={setPassword} />
      {add.error && <Failure error={add.error} title="Not added" />}
      <Group justify="flex-end" gap="sm">
        <Button variant="default" size="sm" onClick={onClose}>
          Cancel
        </Button>
        <Button
          size="sm"
          loading={add.isPending}
          disabled={!name || roles.length === 0 || password.length < 12}
          onClick={() => add.mutate()}
        >
          Add
        </Button>
      </Group>
    </Stack>
  );
}

function EditUser({ user, onClose }: { user: User; onClose: () => void }) {
  const queries = useQueryClient();
  const [roles, setRoles] = useState<string[]>(user.roles);
  const [password, setPassword] = useState("");
  const save = useMutation({
    mutationFn: (body: Schemas["UserUpdate"]) =>
      ok(api.PATCH("/api/v1/admin/users/{name}", { params: { path: { name: user.name } }, body })),
    onSuccess: (u) => {
      void queries.invalidateQueries({ queryKey: ["admin", "users"] });
      notifications.show({ color: "accent", title: `${u.name} changed`, message: "Their sessions have ended." });
      onClose();
    },
  });
  const changed = roles.join() !== user.roles.join();
  return (
    <Stack gap="sm">
      <Text size="sm" c="dimmed">
        A change ends every session of {user.name}.
      </Text>
      <MultiSelect label="Roles" data={ROLES} value={roles} onChange={setRoles} />
      <PasswordField value={password} onChange={setPassword} />
      {save.error && <Failure error={save.error} title="Not changed" />}
      <Group justify="space-between" gap="sm">
        <Button
          variant="default"
          color={user.disabled ? undefined : "red"}
          size="sm"
          loading={save.isPending}
          onClick={() => save.mutate({ disabled: !user.disabled })}
        >
          {user.disabled ? "Enable" : "Disable"}
        </Button>
        <Group gap="sm">
          <Button variant="default" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button
            size="sm"
            loading={save.isPending}
            disabled={(!changed && !password) || roles.length === 0 || (password !== "" && password.length < 12)}
            onClick={() =>
              save.mutate({
                ...(changed ? { roles: roles as Role[] } : {}),
                ...(password ? { password } : {}),
              })
            }
          >
            Save
          </Button>
        </Group>
      </Group>
    </Stack>
  );
}

export default function Users() {
  const me = useMe().data;
  const users = useQuery({ queryKey: ["admin", "users"], queryFn: () => ok(api.GET("/api/v1/admin/users")) });
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<User | null>(null);
  const list = [...(users.data?.users ?? [])].sort((a, b) => a.name.localeCompare(b.name));
  return (
    <SpacePage>
      <Panel
        title="Accounts"
        hint={`${list.length} accounts; passwords are hashed with argon2id`}
        actions={
          <Button size="xs" onClick={() => setAdding(true)}>
            Add a user
          </Button>
        }
        flush
      >
        {users.isPending && <Loading />}
        {users.error && <Failure error={users.error} />}
        <DataTable
          caption="The desk's accounts"
          head={["Name", "Roles", "State", "Added", "Password set", ""]}
          rows={list.map((u) => ({
            key: u.user_id,
            cells: [
              <span key="n" className={c.mono}>
                {u.name}
                {u.name === me?.name ? " (you)" : ""}
              </span>,
              <Group key="r" gap={4}>
                {u.roles.map((r) => (
                  <Badge key={r} size="xs" variant="outline">
                    {r}
                  </Badge>
                ))}
              </Group>,
              u.disabled
                ? "disabled"
                : u.locked_until && new Date(u.locked_until) > new Date()
                  ? `locked until ${new Date(u.locked_until).toLocaleTimeString("en-GB")}`
                  : "active",
              formatDate(u.created_at),
              formatDate(u.password_changed_at),
              <Button
                key="e"
                size="compact-xs"
                variant="subtle"
                onClick={() => setEditing(u)}
                aria-label={`Change ${u.name}`}
              >
                Change
              </Button>,
            ],
          }))}
        />
      </Panel>
      <Modal opened={adding} onClose={() => setAdding(false)} title="Add a user">
        {adding && <AddUser onClose={() => setAdding(false)} />}
      </Modal>
      <Modal
        opened={editing !== null}
        onClose={() => setEditing(null)}
        title={editing ? `Change ${editing.name}` : "Change an account"}
      >
        {editing && <EditUser key={editing.user_id} user={editing} onClose={() => setEditing(null)} />}
      </Modal>
    </SpacePage>
  );
}
