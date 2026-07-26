import { useState, useEffect, useRef } from "react";
import { Plus, Trash2 } from "lucide-react";
import { settingsApi } from "../services/api";
import type { InstructionItem } from "../services/api";
import toast from "react-hot-toast";
import "./SettingsPage.css";

function AutoResizeTextarea({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (val: string) => void;
  placeholder: string;
}) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${textareaRef.current.scrollHeight}px`;
    }
  }, [value]);

  return (
    <textarea
      ref={textareaRef}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      rows={1}
      className="auto-resize-textarea"
    />
  );
}

const CATEGORIES = [
  { id: "rules", label: "Строгие правила" },
  { id: "recommendations", label: "Рекомендации" },
  { id: "context", label: "Контекст и Роль" },
];

const DEFAULT_INSTRUCTIONS: InstructionItem[] = [
  // Строгие правила
  {
    category: "rules",
    content: "ОБЯЗАТЕЛЬНО пиши весь текст исключительно на русском языке.",
  },

  {
    category: "rules",
    content:
      "Форматируй большие числа в человеко-понятный текстовый вид, а рядом в скобках указывай точное число. Например: 3.04 млрд (3041454711).",
  },
  {
    category: "rules",
    content:
      "Абсолютная точность: используй строго те значения, которые предоставлены в данных.",
  },
  {
    category: "rules",
    content:
      "Категорически запрещено придумывать цифры, искажать метрики и галлюцинировать.",
  },
  {
    category: "rules",
    content:
      "Запрещено строить догадки о причинах изменения метрик, если этих причин нет в выборке.",
  },
  {
    category: "rules",
    content:
      "Не используй [Жирный шрифт] или markdown-форматирование текста звездами (**)",
  },
  {
    category: "rules",
    content:
      "При ответах на запросы о ключевых показателях (KPI) розничных сетей обязательно указывай: выручку, прибыль, маржинальность (%), средний чек, количество заказов/транзакций, количество проданных единиц. Если данные содержат информацию о магазинах/точках — добавляй разбивку по точкам продаж. Сравнивай показатели с предыдущим периодом если данные позволяют.",
  },
  {
    category: "rules",
    content:
      "При работе с группами баз данных (многотабличные .db файлы): определи какие таблицы содержат факты продаж (даты, суммы, количества), а какие — справочники (названия, категории, адреса). Соединяй таблицы через связи из системного промпта. Для агрегирующих запросов ищи числовые колонки в таблицах фактов. Если запрос касается названий или категорий — JOIN со справочниками.",
  },
  {
    category: "rules",
    content:
      "При анализе данных розничных сетей: выводи числа как есть, БЕЗ указания валюты, если она явно не указана в базе. Используй бизнес-терминологию: товарооборот, маржинальность, средний чек, конверсия, ROMI. При выявлении аномалий (резкие скачки/падения) — объясняй возможные причины и давай рекомендации. При сравнении периодов — указывай процент изменения.",
  },
  {
    category: "rules",
    content:
      "ОЧЕНЬ ВАЖНО ДЛЯ ДАТ: В DuckDB если колонка даты — это UNIX timestamp (BIGINT, целое число), конвертируй её ТОЛЬКО через TO_TIMESTAMP(col). НИКОГДА не вычитай INTERVAL из BIGINT! Правильно: TO_TIMESTAMP(col) - INTERVAL '30 DAY'. Если же колонка даты — это строка (VARCHAR), конвертируй её ТОЛЬКО через TRY_CAST(col AS DATE). НИКОГДА не путай их!",
  },
  {
    category: "rules",
    content:
      "ОТСУТСТВИЕ КОНТЕКСТА: Если запрос бессмысленный или абстрактный (например, 'TOP N' без указания чего именно) и ты НЕ МОЖЕШЬ составить SQL, начни свой ответ строго со слова CLARIFY: и напиши уточняющий вопрос к пользователю.",
  },

  // Рекомендации
  {
    category: "recommendations",
    content:
      "Используй профессиональную лексику из сферы продаж, маркетинга и бизнес-аналитики.",
  },
  {
    category: "recommendations",
    content:
      "Фокусируйся на конкретных точках роста: подсвечивай аномалии, лидеров и аутсайдеров (товары, категории, менеджеры, регионы).",
  },
  {
    category: "recommendations",
    content:
      "Делай ответ легко читаемым: выделяй ключевые KPI жирным шрифтом и используй маркированные списки.",
  },
  {
    category: "recommendations",
    content:
      "При анализе продаж розничной сети учитывай: 1) Структуру данных — продажи (факты) и справочники (товары, магазины, клиенты). 2) Многотабличные данные требуют JOIN через связи. 3) Числовые суммы всегда округляй ROUND(). 4) Для динамики используй GROUP BY по дате с ORDER BY. 5) Для поиска товаров/категорий используй LOWER() LIKE для регистронезависимого поиска.",
  },
  {
    category: "recommendations",
    content:
      "Популярные аналитические запросы для розничных сетей и как на них отвечать: 1) Топ товаров/категорий — GROUP BY + ORDER BY SUM(revenue) DESC LIMIT N. 2) Средний чек — AVG(revenue) WHERE revenue > 0. 3) Динамика продаж — GROUP BY DATE_TRUNC(month/day) с ORDER BY. 4) Продажи по регионам/магазинам — GROUP BY region/store. 5) ABC-анализ — ранжирование по выручке с накопительным процентом. 6) Возвраты — WHERE returns > 0 или CASE WHEN. 7) Влияние скидок — GROUP BY discount с AVG(revenue). 8) Конверсия — COUNT с CASE WHEN условиями.",
  },

  // Контекст и Роль
  {
    category: "context",
    content: "Ты — Senior Business & Sales Analyst (Старший бизнес-аналитик).",
  },
  {
    category: "context",
    content:
      "Твоя цель — помогать коммерческому директору и отделу маркетинга быстро и точно понимать текущую ситуацию по продажам.",
  },
  {
    category: "context",
    content:
      "Ты смотришь на данные через призму выручки, прибыли и эффективности продаж.",
  },
  {
    category: "context",
    content:
      "Если данных для ответа на вопрос пользователя нет в базе — прямо и вежливо скажи об этом, не пытайся угадать ответ.",
  },
];

export default function SettingsPage() {
  const [instructions, setInstructions] = useState<InstructionItem[]>([]);
  const [ttsVoice, setTtsVoice] = useState<string>("ru-RU-SvetlanaNeural");
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    fetchData();
  }, []);

  const fetchData = async () => {
    setIsLoading(true);
    try {
      const resInst = await settingsApi.getInstructions();
      setInstructions(
        Array.isArray(resInst?.instructions) ? resInst.instructions : [],
      );
    } catch (e) {
      console.error("Settings load error:", e);
    }
    try {
      const config = await settingsApi.getConfig();
      if (config && config.tts_voice) {
        setTtsVoice(config.tts_voice);
      }
    } catch (e) {
      console.error("Config load error:", e);
    }
    setIsLoading(false);
  };

  const handleSave = async () => {
    setIsSaving(true);
    try {
      await settingsApi.saveInstructions({
        instructions: instructions.filter((i) => i.content.trim() !== ""),
      });
      await settingsApi.saveConfig("tts_voice", ttsVoice);
      toast.success("Настройки успешно сохранены");
    } catch (e) {
      console.error(e);
      toast.error("Ошибка при сохранении");
    } finally {
      setIsSaving(false);
    }
  };

  const handleRestoreDefaults = async () => {
    setInstructions([...DEFAULT_INSTRUCTIONS]);
    setIsSaving(true);
    try {
      await settingsApi.saveInstructions({
        instructions: DEFAULT_INSTRUCTIONS,
      });
      toast.success("Настройки по умолчанию восстановлены и сохранены");
    } catch (e) {
      console.error(e);
      toast.error("Ошибка при сохранении");
    } finally {
      setIsSaving(false);
    }
  };

  const handleAddInstruction = (category: string) => {
    setInstructions([...instructions, { category, content: "" }]);
  };

  const handleUpdateInstruction = (index: number, content: string) => {
    const newInst = [...instructions];
    newInst[index].content = content;
    setInstructions(newInst);
  };

  const handleDeleteInstruction = (index: number) => {
    const newInst = [...instructions];
    newInst.splice(index, 1);
    setInstructions(newInst);
  };

  return (
    <div className="scrollable-page">
      <div className="settings-page page-container">
        <header className="page-header">
          <h1>Настройки AI-Ассистента</h1>
          <p>
            Управляйте тем, как ассистент будет формировать ответы и общаться с
            вами.
          </p>
        </header>

        <div className="settings-content">
          {CATEGORIES.map((cat) => {
            const catInstructions = instructions
              .map((inst, index) => ({ inst, index }))
              .filter((item) => item.inst.category === cat.id);

            return (
              <div key={cat.id} className="settings-section">
                <h2>{cat.label}</h2>
                {isLoading ? (
                  <div className="settings-loading">
                    <div className="spinner"></div>
                  </div>
                ) : (
                  <div className="instruction-list">
                    {catInstructions.map(({ inst, index }) => (
                      <div key={index} className="instruction-item">
                        <AutoResizeTextarea
                          value={inst.content}
                          onChange={(val) =>
                            handleUpdateInstruction(index, val)
                          }
                          placeholder={`Введите правило для категории: ${cat.label}...`}
                        />
                        <button
                          className="btn btn--tertiary btn--alert btn--icon"
                          onClick={() => handleDeleteInstruction(index)}
                          title="Удалить"
                        >
                          <Trash2 size={18} />
                        </button>
                      </div>
                    ))}
                    <button
                      className="btn btn--secondary"
                      onClick={() => handleAddInstruction(cat.id)}
                    >
                      <Plus size={18} />
                      Добавить правило
                    </button>
                  </div>
                )}
              </div>
            );
          })}

          <div className="settings-section">
            <h2>Голос ассистента</h2>
            {isLoading ? (
              <div className="settings-loading">
                <div className="spinner"></div>
              </div>
            ) : (
              <div className="instruction-list">
                <div className="instruction-item instruction-item--naked">
                  <select
                    value={ttsVoice}
                    onChange={(e) => setTtsVoice(e.target.value)}
                    className="settings-select"
                  >
                    <option value="ru-RU-SvetlanaNeural">Женский</option>
                    <option value="ru-RU-DmitryNeural">Мужской</option>
                  </select>
                </div>
              </div>
            )}
          </div>

          <div className="settings-actions">
            <button
              className="btn btn--secondary"
              onClick={handleRestoreDefaults}
              disabled={isSaving}
            >
              Восстановить умолчания
            </button>
            <button
              className="btn btn--primary"
              onClick={handleSave}
              disabled={isSaving}
            >
              {isSaving ? "Сохранение..." : "Сохранить изменения"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
