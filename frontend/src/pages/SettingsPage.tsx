import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Plus, RotateCcw, Trash2 } from "lucide-react";
import toast from "react-hot-toast";
import ModelManager from "../components/ModelManager";
import { settingsApi } from "../shared/api/settings.api";
import type { InstructionItem } from "../shared/api/settings.api";
import { useConfirm } from "../shared/providers/ConfirmContext";
import { Button, ErrorState, Field, IconButton, Input, Page, PageHeader, Panel, Segmented, Skeleton, SkeletonLines } from "../shared/ui";
import { formatCount } from "../shared/lib/format";
import "./SettingsPage.css";

type Rule = InstructionItem & { _k: string };

let keySeq = 0;
const withKey = (items: InstructionItem[]): Rule[] => items.map((i) => ({ ...i, _k: `r${++keySeq}` }));
const strip = (items: Rule[]): InstructionItem[] => items.map(({ _k, ...rest }) => { void _k; return rest; });

function AutoResizeTextarea({ value, onChange, label }: { value: string; onChange: (val: string) => void; label: string }) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (ref.current) {
      ref.current.style.height = "auto";
      ref.current.style.height = `${ref.current.scrollHeight}px`;
    }
  }, [value]);
  return <textarea ref={ref} value={value} onChange={(e) => onChange(e.target.value)} placeholder="Текст правила" aria-label={label} rows={1} className="rules__input" />;
}

const CATEGORIES = [
  { id: "rules", label: "Строгие правила", description: "Ассистент обязан их соблюдать" },
  { id: "recommendations", label: "Рекомендации", description: "Как формулировать и оформлять ответы" },
  { id: "context", label: "Контекст и роль", description: "Кто ассистент и для кого отвечает" },
];

const RULES_FORMS: [string, string, string] = ["правило", "правила", "правил"];

// Длина ограничена и на бэкенде: валюта попадает в промпт модели
const CURRENCY_MAX = 32;
const CURRENCY_SUGGESTIONS = ["тенге", "рубли", "сом", "сум", "доллары", "евро"];

export default function SettingsPage() {
  const confirm = useConfirm();
  const [instructions, setInstructions] = useState<Rule[]>([]);
  const [ttsVoice, setTtsVoice] = useState<string>("ru-RU-SvetlanaNeural");
  // Пустая строка — валюта по умолчанию из .env (currencyDefault)
  const [currency, setCurrency] = useState<string>("");
  const [currencyDefault, setCurrencyDefault] = useState<string>("тенге");
  const [saved, setSaved] = useState<string>("");
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  // Голос и валюта не загрузились: на экране были бы значения по умолчанию, и «Сохранить» затёрло бы
  // ими сохранённые (пустая валюта на бэкенде — удаление своей). Пока так, эти ключи не отправляем
  const [configError, setConfigError] = useState(false);
  const [isSaving, setIsSaving] = useState(false);

  const snapshot = useCallback(
    (items: Rule[], voice: string, cur: string) => JSON.stringify({ i: strip(items).filter((x) => x.content.trim() !== ""), v: voice, c: cur.trim() }),
    [],
  );

  // null — не загрузились
  const loadConfig = useCallback(async (): Promise<{ voice: string; cur: string } | null> => {
    try {
      const config = await settingsApi.getConfig();
      if (config?.local_currency_default) setCurrencyDefault(config.local_currency_default);
      return { voice: config?.tts_voice || "ru-RU-SvetlanaNeural", cur: config?.local_currency || "" };
    } catch (e) {
      console.error("Config load error:", e);
      return null;
    }
  }, []);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    setLoadError(false);
    let items: Rule[] = [];
    try {
      const resInst = await settingsApi.getInstructions();
      items = withKey(Array.isArray(resInst?.instructions) ? resInst.instructions : []);
    } catch (e) {
      console.error("Settings load error:", e);
      setLoadError(true);
    }
    const config = await loadConfig();
    const voice = config?.voice ?? "ru-RU-SvetlanaNeural";
    const cur = config?.cur ?? "";
    setConfigError(config === null);
    setInstructions(items);
    setTtsVoice(voice);
    setCurrency(cur);
    setSaved(snapshot(items, voice, cur));
    setIsLoading(false);
  }, [snapshot, loadConfig]);

  // Повтор только для голоса и валюты: несохранённые правки правил не теряются
  const retryConfig = async () => {
    const config = await loadConfig();
    if (!config) {
      toast.error("Не удалось загрузить голос и валюту");
      return;
    }
    setTtsVoice(config.voice);
    setCurrency(config.cur);
    // В снимке — сохранённые правила и только что загруженные голос и валюта
    const prev = saved ? (JSON.parse(saved) as { i?: InstructionItem[] }) : {};
    setSaved(JSON.stringify({ i: prev.i ?? [], v: config.voice, c: config.cur.trim() }));
    setConfigError(false);
  };

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const dirty = useMemo(
    () => !isLoading && snapshot(instructions, ttsVoice, currency) !== saved,
    [instructions, ttsVoice, currency, saved, snapshot, isLoading],
  );

  const handleSave = async () => {
    setIsSaving(true);
    try {
      const clean = instructions.filter((i) => i.content.trim() !== "");
      await settingsApi.saveInstructions({ instructions: strip(clean) });
      const cur = currency.trim();
      // Без загруженных значений голос и валюта на экране — умолчания, а не выбор пользователя
      if (!configError) {
        await settingsApi.saveConfig("tts_voice", ttsVoice);
        await settingsApi.saveConfig("local_currency", cur);
      }
      setInstructions(clean);
      setCurrency(cur);
      setSaved(snapshot(clean, ttsVoice, cur));
      toast.success("Настройки сохранены");
    } catch (e) {
      console.error(e);
      toast.error("Не удалось сохранить настройки");
    } finally {
      setIsSaving(false);
    }
  };

  const handleRestoreDefaults = async () => {
    const ok = await confirm(
      "Все текущие правила будут заменены стандартным набором и сразу сохранены. Голос озвучки и валюта не изменятся.",
      "Восстановить стандартные правила?",
      { confirmLabel: "Восстановить" },
    );
    if (!ok) return;
    setIsSaving(true);
    try {
      // Стандартный набор — с сервера: тот же, что получает новая база
      const defaults = (await settingsApi.getDefaultInstructions()).instructions;
      await settingsApi.saveInstructions({ instructions: defaults });
      const items = withKey(defaults);
      setInstructions(items);
      // Голос и валюта этой кнопкой не сохраняются: в снимке оставляем сохранённые значения,
      // чтобы их несохранённая смена по-прежнему держала панель «Сохранить»
      const prev = saved ? (JSON.parse(saved) as { v?: string; c?: string }) : {};
      setSaved(snapshot(items, prev.v ?? ttsVoice, prev.c ?? currency));
      // Правила теперь известны — ошибка первичной загрузки больше не актуальна
      setLoadError(false);
      toast.success("Стандартные правила восстановлены");
    } catch (e) {
      console.error(e);
      toast.error("Не удалось восстановить правила");
    } finally {
      setIsSaving(false);
    }
  };

  const addRule = (category: string) => setInstructions((prev) => [...prev, ...withKey([{ category, content: "" }])]);
  const updateRule = (key: string, content: string) => setInstructions((prev) => prev.map((r) => (r._k === key ? { ...r, content } : r)));
  const removeRule = (key: string) => setInstructions((prev) => prev.filter((r) => r._k !== key));

  // Кнопка нужна и при ошибке загрузки: восстановить стандартный набор — способ выбраться из неё
  const restoreButton = (
    <Button variant="ghost" size="sm" icon={<RotateCcw />} onClick={handleRestoreDefaults} disabled={isSaving || isLoading}>
      Стандартные
    </Button>
  );

  return (
    <Page width="narrow" className="rules">
      <PageHeader
        title="Настройки ассистента"
        description="Модель переключается сразу, остальное — после сохранения"
      />

      {/* Сама сохраняет выбор: скачивание и переключение модели не ждут кнопки «Сохранить» */}
      <ModelManager />

      {loadError ? (
        <Panel title="Правила для модели" actions={restoreButton}>
          <ErrorState title="Не удалось загрузить правила" onRetry={fetchData} />
        </Panel>
      ) : (
        <>
          <Panel title="Ответы" description="Как ассистент озвучивает ответы и подписывает суммы">
            {configError && !isLoading ? (
              <ErrorState compact title="Не удалось загрузить голос и валюту" description="Правила можно сохранять, голос и валюта не изменятся." onRetry={retryConfig} />
            ) : isLoading ? (
              <div className="rules__answer-grid">
                <Skeleton width={200} height={28} />
                <Skeleton width={240} height={32} />
              </div>
            ) : (
              <div className="rules__answer-grid">
                <div className="ui-field">
                  <span className="ui-field__label" id="tts-voice-label">Голос озвучки</span>
                  <Segmented
                    label="Голос озвучки"
                    value={ttsVoice}
                    onChange={setTtsVoice}
                    options={[
                      { value: "ru-RU-SvetlanaNeural", label: "Женский" },
                      { value: "ru-RU-DmitryNeural", label: "Мужской" },
                    ]}
                  />
                  <p className="ui-field__hint">Когда в чате включена озвучка</p>
                </div>
                <Field label="Валюта" hint={`Если валюты нет в данных. Пусто — ${currencyDefault}`}>
                  {(id, describedBy) => (
                    <>
                      <Input
                        id={id}
                        aria-describedby={describedBy}
                        value={currency}
                        onChange={(e) => setCurrency(e.target.value)}
                        placeholder={currencyDefault}
                        maxLength={CURRENCY_MAX}
                        list="currency-suggestions"
                        autoComplete="off"
                      />
                      <datalist id="currency-suggestions">
                        {CURRENCY_SUGGESTIONS.map((c) => (
                          <option key={c} value={c} />
                        ))}
                      </datalist>
                    </>
                  )}
                </Field>
              </div>
            )}
          </Panel>

          <Panel
            padding="none"
            title="Правила для модели"
            description="Добавляются к каждому вопросу. Чем короче список, тем точнее модель его соблюдает"
            actions={restoreButton}
          >
            {isLoading ? (
              <SkeletonLines className="rules__skeleton" widths={["80%", "64%", "72%"]} />
            ) : (
              CATEGORIES.map((cat) => {
                const items = instructions.filter((r) => r.category === cat.id);
                return (
                  <section key={cat.id} className="rules__group" aria-label={cat.label}>
                    <div className="rules__group-head">
                      <h3 className="rules__group-title">{cat.label}</h3>
                      <span className="rules__group-desc">
                        {cat.description} · {formatCount(items.length, RULES_FORMS)}
                      </span>
                    </div>
                    <ol className="rules__list">
                      {items.map((r, i) => (
                        <li key={r._k} className="rules__item">
                          <span className="rules__num">{i + 1}</span>
                          <AutoResizeTextarea value={r.content} onChange={(v) => updateRule(r._k, v)} label={`${cat.label}, правило ${i + 1}`} />
                          <IconButton size="sm" variant="danger" label="Удалить правило" icon={<Trash2 />} onClick={() => removeRule(r._k)} />
                        </li>
                      ))}
                    </ol>
                    <div className="rules__add">
                      <Button size="sm" variant="ghost" icon={<Plus />} onClick={() => addRule(cat.id)}>
                        Добавить правило
                      </Button>
                    </div>
                  </section>
                );
              })
            )}
          </Panel>

          <div className={`rules__savebar ${dirty ? "is-visible" : ""}`} aria-hidden={!dirty}>
            <span>Есть несохранённые изменения</span>
            <div className="rules__savebar-actions">
              <Button variant="ghost" size="sm" onClick={fetchData} disabled={isSaving} tabIndex={dirty ? 0 : -1}>
                Отменить
              </Button>
              <Button variant="primary" size="sm" onClick={handleSave} loading={isSaving} tabIndex={dirty ? 0 : -1}>
                Сохранить
              </Button>
            </div>
          </div>
        </>
      )}
    </Page>
  );
}
