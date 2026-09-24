import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { BookOpen, CircleCheck, FileText, GraduationCap, HelpCircle, MessageCircle, PlayCircle, RotateCcw, Send, Sparkles, Users, XCircle, type LucideIcon } from 'lucide-react';
import { Button } from '@/components/Button';
import { DataState } from '@/components/DataState';
import { StatusBadge } from '@/components/StatusBadge';
import { chatBusyMessage, chatSuggestions, saveTrainingResult, sendChat, useTraining, uuid } from '@/data/hooks';
import { cx } from '@/lib/cx';
import { fmtDate } from '@/lib/format';
import { useConnection } from '@/stores/connection';
import type { ChatSource, Training, TrainingFormat, TrainingModule } from '@/types/domain';
import { OperatorShell, useCab } from './Shell';
import { useT, type Key, type T } from '@/i18n';

type Tab = 'for-you' | 'library' | 'practice' | 'ask';
const TABS: Array<[Tab, Key, LucideIcon]> = [
  ['for-you', 'training.forYou', Sparkles],
  ['library', 'training.library', BookOpen],
  ['practice', 'training.practice', GraduationCap],
  ['ask', 'training.ask', MessageCircle],
];

const FORMAT: Record<TrainingFormat, { icon: LucideIcon; word: Key }> = {
  video: { icon: PlayCircle, word: 'format.video' },
  document: { icon: FileText, word: 'format.document' },
  quiz: { icon: HelpCircle, word: 'format.quiz' },
  scenario: { icon: GraduationCap, word: 'format.scenario' },
  instructor_session: { icon: Users, word: 'format.instructor_session' },
};

export const SIM_MODULE = 'TM-SIM-01';

export function OperatorTraining() {
  const t = useT();
  const cab = useCab();
  const res = useTraining(cab?.operatorId);
  const [tab, setTab] = useState<Tab>(() => {
    const q = new URLSearchParams(window.location.search).get('tab');
    return TABS.some(([k]) => k === q) ? (q as Tab) : 'for-you';
  });
  return (
    <OperatorShell>
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-cab-h1">{t('training.title')}</h1>
        <div className="flex gap-2" role="tablist" aria-label={t('training.title')}>
          {TABS.map(([k, label]) => (
            <Button key={k} role="tab" aria-selected={tab === k} variant={tab === k ? 'primary' : 'secondary'} onClick={() => setTab(k)} className="whitespace-nowrap px-5">
              {t(label)}
            </Button>
          ))}
        </div>
      </div>
      {tab === 'ask' ? (
        <Chat />
      ) : (
        <DataState
          res={res}
          isEmpty={(d) => !d || d.modules.length === 0}
          errorTitle={t('training.loadError')}
          empty={{ icon: GraduationCap, title: t('training.empty'), hint: t('training.emptyHint') }}
        >
          {(d) =>
            tab === 'for-you' ? <ForYou t={d} onPractice={() => setTab('practice')} /> : tab === 'library' ? <Library t={d} /> : <Practice t={d} />
          }
        </DataState>
      )}
    </OperatorShell>
  );
}

function progress(tr: T, t: Training, moduleId: string) {
  const recs = t.records.filter((r) => r.module_id === moduleId);
  const done = recs.find((r) => r.completed_at);
  if (done) {
    const date = fmtDate(done.completed_at);
    return { word: done.score != null ? tr('training.doneOnScore', { date, score: Math.round(done.score) }) : tr('training.doneOn', { date }), done: true };
  }
  return recs.length ? { word: tr('training.started'), done: false } : null;
}

function ModuleRow({ m, t, action }: { m: TrainingModule; t: Training; action?: ReactNode }) {
  const tr = useT();
  const f = FORMAT[m.format];
  const p = progress(tr, t, m.module_id);
  return (
    <li className="flex items-center justify-between gap-4 rounded-md border bg-surface px-6 py-4">
      <div className="flex min-w-0 items-center gap-4">
        <f.icon size={32} className="shrink-0 text-ink-2" aria-hidden />
        <div className="min-w-0">
          <p className="truncate text-cab-body">{m.title}</p>
          <p className="text-cab-small text-ink-2">
            {tr(f.word)}
            {m.duration_min != null && (
              <>
                {' '}
                · <span className="reading">{m.duration_min}</span> {tr('unit.min')}
              </>
            )}
          </p>
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-3">
        {p && <StatusBadge status={p.done ? 'ok' : 'info'} label={p.word} />}
        {action}
      </div>
    </li>
  );
}

function ForYou({ t, onPractice }: { t: Training; onPractice: () => void }) {
  const tr = useT();
  const recs = t.recommendations.filter((r) => r.status === 'pending');
  if (recs.length === 0) return <p className="text-cab-body text-ink-2">{tr('training.nothing')}</p>;
  return (
    <ul className="flex flex-col gap-3" aria-label={tr('training.suggestedAria')}>
      {recs.map((r) => {
        const m = t.modules.find((x) => x.module_id === r.module_id);
        if (!m) return null;
        return (
          <li key={r.id} className="flex flex-col gap-3 rounded-md border bg-surface p-6">
            <p className="text-cab-body">{r.reason}</p>
            <ul>
              <ModuleRow
                m={m}
                t={t}
                action={
                  m.module_id === SIM_MODULE ? (
                    <Button icon={GraduationCap} onClick={onPractice}>
                      {tr('training.practise')}
                    </Button>
                  ) : (
                    <Button icon={FORMAT[m.format].icon}>{tr('training.start')}</Button>
                  )
                }
              />
            </ul>
          </li>
        );
      })}
    </ul>
  );
}

function Library({ t }: { t: Training }) {
  const topics = useMemo(() => [...new Set(t.modules.map((m) => m.topic))], [t.modules]);
  return (
    <div className="flex flex-col gap-4">
      {topics.map((topic) => (
        <section key={topic} className="flex flex-col gap-2">
          <h2 className="text-cab-h2 capitalize text-ink-2">{topic}</h2>
          <ul className="flex flex-col gap-2">
            {t.modules
              .filter((m) => m.topic === topic)
              .map((m) => (
                <ModuleRow key={m.module_id} m={m} t={t} />
              ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

/** Scenario simulator (features.md §4.5): TM-SIM-01, one step at a time, scored with explanations. */
function Practice({ t }: { t: Training }) {
  const tr = useT();
  const cab = useCab();
  const sim = t.modules.find((m) => m.module_id === SIM_MODULE);
  const steps = sim?.scenario?.steps ?? [];
  const [i, setI] = useState(0);
  const [picked, setPicked] = useState<number | null>(null);
  const [right, setRight] = useState(0);
  const [saved, setSaved] = useState(false);

  if (!sim || steps.length === 0)
    return <p className="text-cab-body text-ink-2">{tr('training.noScenarios')}</p>;

  if (i >= steps.length) {
    const score = Math.round((right / steps.length) * 100);
    return (
      <section className="flex flex-col gap-4 rounded-md border bg-surface p-6" aria-live="polite">
        <p className="text-cab-small text-ink-2">{sim.title}</p>
        <p className="text-cab-h1">
          {tr.rich('training.rightOf', { right: <span className="reading">{right}</span>, total: <span className="reading">{steps.length}</span> })}
        </p>
        <StatusBadge status={score >= 70 ? 'ok' : 'warning'} label={score >= 70 ? tr('training.passed') : tr('training.tryToPass')} />
        <div className="flex gap-4">
          <Button
            icon={RotateCcw}
            variant="secondary"
            onClick={() => {
              setI(0);
              setRight(0);
              setPicked(null);
              setSaved(false);
            }}
          >
            {tr('training.again')}
          </Button>
          {!saved && cab && (
            <Button
              icon={CircleCheck}
              onClick={() => {
                void saveTrainingResult(cab.operatorId, sim.module_id, score);
                setSaved(true);
              }}
            >
              {tr('training.save')}
            </Button>
          )}
          {saved && <StatusBadge status="ok" label={tr('training.saved')} />}
        </div>
      </section>
    );
  }

  const step = steps[i]!;
  const answered = picked != null;
  const next = t.modules.find((m) => m.module_id === step.module_id);
  return (
    <section className="flex flex-col gap-4" aria-label={tr('training.scenarioOf', { n: i + 1, total: steps.length })}>
      <div className="flex items-center justify-between">
        <p className="text-cab-small text-ink-2">
          {tr.rich('training.scenarioOf', { n: <span className="reading">{i + 1}</span>, total: <span className="reading">{steps.length}</span> })}
          {step.title && ` · ${step.title}`}
        </p>
        <p className="reading text-cab-small text-ink-2">{tr('training.nRight', { n: right })}</p>
      </div>
      <p className="text-cab-h2">{step.prompt}</p>
      <ol className="flex flex-col gap-3">
        {step.choices.map((c, k) => {
          const isAnswer = k === step.answer;
          const state = !answered ? 'idle' : isAnswer ? 'right' : k === picked ? 'wrong' : 'idle';
          return (
            <li key={k}>
              <button
                type="button"
                disabled={answered}
                onClick={() => {
                  setPicked(k);
                  if (isAnswer) setRight((r) => r + 1);
                }}
                className={cx(
                  'flex min-h-touch-cab w-full items-center justify-between gap-4 rounded-md border px-5 py-3 text-left text-cab-body transition-state',
                  state === 'right' && 'border-ok bg-tint-ok',
                  state === 'wrong' && 'border-critical bg-tint-critical',
                  state === 'idle' && 'border-line bg-surface enabled:hover:border-ink-3',
                )}
              >
                <span>{c}</span>
                {state === 'right' && <StatusBadge status="ok" label={tr('training.right')} className="shrink-0" />}
                {state === 'wrong' && (
                  <span className="flex shrink-0 items-center gap-2 text-cab-small">
                    <XCircle size={24} className="text-critical" aria-hidden /> {tr('training.notThis')}
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ol>
      {answered && (
        <div className="flex flex-col gap-3 rounded-md border bg-raised p-5" aria-live="polite">
          {step.explanation && <p className="text-cab-body">{step.explanation}</p>}
          {next && <p className="text-cab-small text-ink-2">{tr('training.learnMore', { title: next.title })}</p>}
          <Button
            className="self-start"
            onClick={() => {
              setI((x) => x + 1);
              setPicked(null);
            }}
          >
            {i + 1 < steps.length ? tr('training.nextScenario') : tr('training.seeScore')}
          </Button>
        </div>
      )}
    </section>
  );
}

interface Msg {
  role: 'user' | 'assistant';
  text: string;
  sources?: ChatSource[];
}

/** RAG chatbot UI (features.md §4.1): answers with their sources. */
function Chat() {
  const t = useT();
  const cab = useCab();
  const online = useConnection((s) => s.online);
  const [session] = useState(uuid);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [suggest, setSuggest] = useState<string[]>([]);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    void chatSuggestions().then((s) => setSuggest(s.slice(0, 4)));
  }, [t]);
  useEffect(() => {
    end.current?.scrollIntoView({ block: 'end' });
  }, [msgs, busy]);

  const ask = async (q: string) => {
    if (!q.trim() || !cab) return;
    setMsgs((m) => [...m, { role: 'user', text: q }]);
    setText('');
    setBusy(true);
    try {
      const a = await sendChat(cab.operatorId, q, session);
      setMsgs((m) => [...m, { role: 'assistant', text: a.answer, sources: a.sources }]);
    } catch (e) {
      setMsgs((m) => [...m, { role: 'assistant', text: chatBusyMessage(e) ?? t('chat.unreachable') }]);
    } finally {
      setBusy(false);
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    void ask(text);
  };

  return (
    <section className="flex min-h-0 flex-1 flex-col gap-3" aria-label={t('chat.aria')}>
      <div className="flex min-h-[200px] flex-1 flex-col gap-3 overflow-auto rounded-md border bg-surface p-4" aria-live="polite">
        {msgs.length === 0 && (
          <div className="flex flex-col gap-3">
            <p className="text-cab-body text-ink-2">{t('chat.intro')}</p>
            <div className="flex flex-wrap gap-2">
              {suggest.map((s) => (
                <Button key={s} variant="secondary" onClick={() => void ask(s)} disabled={!online}>
                  {s}
                </Button>
              ))}
            </div>
          </div>
        )}
        {msgs.map((m, k) => (
          <div key={k} className={cx('flex max-w-[90%] flex-col gap-2 rounded-md px-4 py-3', m.role === 'user' ? 'self-end bg-raised' : 'self-start border bg-bg')}>
            <p className="text-cab-body">{m.text}</p>
            {m.sources && m.sources.length > 0 && (
              <p className="flex flex-wrap items-center gap-2 text-cab-small text-ink-2">
                <BookOpen size={22} aria-hidden /> {t('chat.source')}
                {m.sources.map((s) => (
                  <span key={`${s.document_id}-${s.chunk_index}`} className="rounded-sm border bg-surface px-2">
                    {s.title} · {t.rich('chat.part', { n: <span className="reading">{s.chunk_index + 1}</span> })}
                  </span>
                ))}
              </p>
            )}
          </div>
        ))}
        {busy && <p className="text-cab-small text-ink-2">{t('chat.looking')}</p>}
        <div ref={end} />
      </div>
      <form onSubmit={submit} className="flex gap-3">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={online ? t('chat.placeholder') : t('chat.offline')}
          disabled={!online}
          aria-label={t('chat.question')}
          className="min-h-touch-cab flex-1 rounded-md border border-line bg-raised px-4 text-cab-body text-ink placeholder:text-ink-3 focus:border-saffron-500"
        />
        <Button type="submit" icon={Send} disabled={busy || !text.trim() || !online}>
          {t('chat.ask')}
        </Button>
      </form>
    </section>
  );
}
