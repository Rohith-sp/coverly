export function ErrorBox({ message, children }: { message: string; children?: React.ReactNode }) {
  return (
    <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-900 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
      <p>{message}</p>
      {children}
    </div>
  );
}
