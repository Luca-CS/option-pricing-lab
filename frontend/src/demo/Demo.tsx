// The demo's own pages (build mode `demo`): the first page, where the visitor picks a profile and its story; the
// banner every screen carries (synthetic data, its date, read-only); and the panel that walks a story step by step.
import { Anchor, Button } from "@mantine/core";
import { IconArrowLeft, IconArrowRight, IconChevronDown, IconX } from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Navigate, useNavigate } from "react-router";
import { homeOf } from "../app/spaces";
import { ME, useMe } from "../auth/session";
import { formatDate } from "../lib/format";
import classes from "./Demo.module.css";
import { STORIES, stepPath, storyOf } from "./stories";
import { currentProfile, currentRecording, type Profile, startProfile } from "./transport";

const REPOSITORY = "https://github.com/Luca-CS/option-pricing-lab";
const STEP_KEY = "optlab-demo-step";

type Walk = { profile: Profile; step: number; folded?: boolean } | null;

function readWalk(): Walk {
  try {
    const w = JSON.parse(sessionStorage.getItem(STEP_KEY) ?? "null") as Walk;
    return w && typeof w.step === "number" ? w : null;
  } catch {
    return null;
  }
}

function writeWalk(w: Walk): void {
  try {
    if (w) sessionStorage.setItem(STEP_KEY, JSON.stringify(w));
    else sessionStorage.removeItem(STEP_KEY);
  } catch {
    // nothing kept: the story lasts until the page reloads
  }
  window.dispatchEvent(new Event("optlab-demo-step"));
}

function useWalk(): [Walk, (w: Walk) => void] {
  const [walk, setWalk] = useState<Walk>(readWalk);
  useEffect(() => {
    const changed = () => setWalk(readWalk());
    window.addEventListener("optlab-demo-step", changed);
    return () => window.removeEventListener("optlab-demo-step", changed);
  }, []);
  return [walk, writeWalk];
}

/** The demo's first page: three profiles, each with its story, or a free look around. */
export function DemoLanding() {
  const me = useMe();
  const queries = useQueryClient();
  const navigate = useNavigate();
  const [opening, setOpening] = useState<Profile | null>(null);
  const [failed, setFailed] = useState(false);
  if (me.data && !opening) return <Navigate to={homeOf(me.data as Parameters<typeof homeOf>[0])} replace />;

  async function open(profile: Profile, guided: boolean) {
    setOpening(profile);
    setFailed(false);
    const rec = await startProfile(profile);
    if (!rec) {
      setOpening(null);
      setFailed(true);
      return;
    }
    queries.clear();
    queries.setQueryData(ME, rec.me);
    const story = storyOf(profile);
    const first = story?.steps[0];
    writeWalk(guided && first ? { profile, step: 0 } : null);
    navigate(guided && first ? stepPath(first, rec.refs) : homeOf(rec.me as Parameters<typeof homeOf>[0]), {
      replace: true,
    });
  }

  return (
    <main className={classes.landing}>
      <header className={classes.hero}>
        <div className={classes.brand}>
          <span className={classes.mark} aria-hidden="true">
            o
          </span>
          <span>optlab desk</span>
        </div>
        <h1 className={classes.headline}>An equity derivatives desk, from the term sheet to the VaR</h1>
        <p className={classes.lede}>
          Structured products priced by Monte Carlo under local and stochastic-local vol, a risk engine that revalues
          the book in full, and a market data lake behind it, on one API. This is its front, replaying a session
          recorded on the desk: pick a profile and follow its story, about three minutes each.
        </p>
        <p className={classes.note}>
          Every number here is made up: a synthetic market from a fixed seed, no vendor's data. The demo is read-only.
        </p>
      </header>
      <section className={classes.profiles} aria-label="Profiles">
        {STORIES.map((story) => (
          <article key={story.profile} className={classes.profile} aria-labelledby={`profile-${story.profile}`}>
            <h2 id={`profile-${story.profile}`} className={classes.profileTitle}>
              {story.label}
            </h2>
            <p className={classes.pitch}>{story.pitch}</p>
            <ol className={classes.steps}>
              {story.steps.map((s) => (
                <li key={s.title}>{s.title}</li>
              ))}
            </ol>
            <div className={classes.actions}>
              <Button
                color="amber"
                onClick={() => void open(story.profile, true)}
                loading={opening === story.profile}
                disabled={opening !== null}
              >
                Follow the {story.label.toLowerCase()}'s story
              </Button>
              <Anchor
                component="button"
                type="button"
                size="sm"
                underline="always"
                onClick={() => void open(story.profile, false)}
                disabled={opening !== null}
              >
                Look around freely
              </Anchor>
            </div>
          </article>
        ))}
      </section>
      {failed && (
        <p className={classes.failed}>The recorded session could not be read. Reload the page to try again.</p>
      )}
      <footer className={classes.footer}>
        <Anchor href={REPOSITORY} size="sm" underline="always">
          The repository
        </Anchor>
        <Anchor href={`${REPOSITORY}/tree/main/docs/architecture`} size="sm" underline="always">
          Its architecture
        </Anchor>
        <Anchor href={`${import.meta.env.BASE_URL}lab/`} size="sm" underline="always">
          The pricing lab, computed in your browser
        </Anchor>
      </footer>
    </main>
  );
}

/** Above every screen of the demo: what the numbers are, their date, and that nothing is written. */
export function DemoBanner() {
  const me = useMe().data;
  const date = currentRecording()?.data_date;
  const profile = currentProfile();
  return (
    <div className={classes.banner} role="note" aria-label="Demo">
      <strong>Demo</strong>
      <span>synthetic data{date ? ` at ${formatDate(date)}` : ""} · read-only</span>
      {me && profile && <span className={classes.who}>as the {profile}</span>}
      <span className={classes.grow} />
      <StoryButton />
      <Anchor href={REPOSITORY} size="xs" underline="always" className={classes.bannerLink}>
        Repository
      </Anchor>
    </div>
  );
}

function StoryButton() {
  const [walk, setWalk] = useWalk();
  const navigate = useNavigate();
  const profile = currentProfile();
  const story = storyOf(profile);
  if (!story || walk || !profile) return null;
  return (
    <Anchor
      component="button"
      type="button"
      size="xs"
      underline="always"
      className={classes.bannerLink}
      onClick={() => {
        const first = story.steps[0];
        setWalk({ profile, step: 0 });
        if (first) navigate(stepPath(first, currentRecording()?.refs ?? {}));
      }}
    >
      The {story.label.toLowerCase()}'s story
    </Anchor>
  );
}

/** The card's height, kept as room at the foot of the page: the page's last rows scroll out from under the card,
 * which sits over them (on a phone, over a good part of the screen). */
function useRoom(): [(el: HTMLElement | null) => void, number] {
  const [el, setEl] = useState<HTMLElement | null>(null);
  const [height, setHeight] = useState(0);
  useEffect(() => {
    if (!el) return;
    const watch = new ResizeObserver(() => setHeight(Math.ceil(el.getBoundingClientRect().height)));
    watch.observe(el);
    return () => watch.disconnect();
  }, [el]);
  return [setEl, height];
}

/** The story in progress: its step, what to look at, back and next. */
export function StoryPanel() {
  const [walk, setWalk] = useWalk();
  const navigate = useNavigate();
  const [card, room] = useRoom();
  // below the page, the card's height and its 16 px from the bottom, and as much again to breathe
  const space = <div className={classes.room} style={{ height: room + 32 }} aria-hidden="true" />;
  const story = walk ? storyOf(walk.profile) : undefined;
  const step = story && walk ? story.steps[walk.step] : undefined;
  if (!story || !walk || !step) return null;
  const refs = currentRecording()?.refs ?? {};
  const go = (i: number) => {
    const next = story.steps[i];
    if (!next) return;
    setWalk({ profile: story.profile, step: i });
    navigate(stepPath(next, refs));
  };
  const last = walk.step === story.steps.length - 1;
  const count = `${story.label} · ${walk.step + 1} of ${story.steps.length}`;
  if (walk.folded) {
    // folded to one line, out of the page's way
    return (
      <>
        <aside
          ref={card}
          className={`${classes.story} ${classes.folded}`}
          aria-label={`The ${story.label.toLowerCase()}'s story`}
        >
          <button type="button" className={classes.unfold} onClick={() => setWalk({ ...walk, folded: false })}>
            <span className={classes.storyCount}>{count}</span>
            <span className={classes.foldedTitle}>{step.title}</span>
          </button>
          {!last && (
            <Button size="xs" color="amber" onClick={() => go(walk.step + 1)} aria-label="Next">
              <IconArrowRight size={14} aria-hidden="true" />
            </Button>
          )}
        </aside>
        {space}
      </>
    );
  }
  return (
    <>
      <aside ref={card} className={classes.story} aria-label={`The ${story.label.toLowerCase()}'s story`}>
        <div className={classes.storyHead}>
          <span className={classes.storyCount}>{count}</span>
          <span>
            <button
              type="button"
              className={classes.close}
              onClick={() => setWalk({ ...walk, folded: true })}
              aria-label="Fold the story"
            >
              <IconChevronDown size={14} aria-hidden="true" />
            </button>
            <button type="button" className={classes.close} onClick={() => setWalk(null)} aria-label="Close the story">
              <IconX size={14} aria-hidden="true" />
            </button>
          </span>
        </div>
        <h2 className={classes.storyTitle}>{step.title}</h2>
        <p className={classes.storyBody}>{step.body}</p>
        <div className={classes.storyNav}>
          <Button
            size="xs"
            variant="default"
            leftSection={<IconArrowLeft size={14} aria-hidden="true" />}
            disabled={walk.step === 0}
            onClick={() => go(walk.step - 1)}
          >
            Back
          </Button>
          {last ? (
            <Button size="xs" color="amber" onClick={() => setWalk(null)}>
              Done: look around
            </Button>
          ) : (
            <Button
              size="xs"
              color="amber"
              rightSection={<IconArrowRight size={14} aria-hidden="true" />}
              onClick={() => go(walk.step + 1)}
            >
              Next
            </Button>
          )}
        </div>
      </aside>
      {space}
    </>
  );
}
