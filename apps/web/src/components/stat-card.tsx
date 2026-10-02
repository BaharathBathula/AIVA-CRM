import { ArrowDownRight, ArrowUpRight } from "lucide-react";

type StatCardProps = {
  label: string;
  value: string;
  change: number;
  description: string;
};

export function StatCard({
  label,
  value,
  change,
  description,
}: StatCardProps) {
  const positive = change >= 0;

  return (
    <article className="statCard">
      <div className="statLabel">{label}</div>

      <div className="statValue">{value}</div>

      <div className="statMeta">
        <span className={positive ? "positive" : "negative"}>
          {positive ? (
            <ArrowUpRight size={15} />
          ) : (
            <ArrowDownRight size={15} />
          )}

          {Math.abs(change)}%
        </span>

        <span>{description}</span>
      </div>
    </article>
  );
}
