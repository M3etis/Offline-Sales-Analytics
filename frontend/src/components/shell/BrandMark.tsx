/** Знак: страница ведомости с тремя строками разной длины. Один цвет, без градиентов. */
export function BrandMark({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" fill="none" aria-hidden="true" className="brand-mark">
      <rect x="1" y="1" width="18" height="18" rx="5" fill="var(--accent)" />
      <path d="M5.5 7h5M5.5 10h7.5M5.5 13h9" stroke="var(--on-accent)" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}
