export function Loading({ label = "Loading..." }: { label?: string }) {
  return (
    <p role="status" className="text-sm text-zinc-600 dark:text-zinc-400">
      {label}
    </p>
  );
}
