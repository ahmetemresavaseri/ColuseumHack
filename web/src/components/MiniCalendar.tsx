/**
 * Compact monthly calendar — drops into the Slots panel.
 *
 * - If a booked Date is provided, the calendar opens on that month and highlights
 *   the booked day.
 * - Otherwise it shows the current month and just dims today.
 * - The user can nudge forward / back a month with the chevron buttons.
 */
import { useMemo, useState } from "react";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function startOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), 1);
}

function addMonths(d: Date, n: number): Date {
  return new Date(d.getFullYear(), d.getMonth() + n, 1);
}

function isSameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

type Cell = { date: Date; inMonth: boolean };

function buildGrid(viewMonth: Date): Cell[] {
  const first = startOfMonth(viewMonth);
  // Monday-first: shift so Monday=0, ..., Sunday=6
  const lead = (first.getDay() + 6) % 7;
  const start = new Date(first);
  start.setDate(first.getDate() - lead);
  const cells: Cell[] = [];
  for (let i = 0; i < 42; i++) {
    const day = new Date(start);
    day.setDate(start.getDate() + i);
    cells.push({ date: day, inMonth: day.getMonth() === viewMonth.getMonth() });
  }
  // Trim a trailing row if it's entirely the next month.
  if (cells.slice(35).every((c) => !c.inMonth)) cells.length = 35;
  return cells;
}

export function MiniCalendar({ bookedDate }: { bookedDate: Date | null }) {
  const today = useMemo(() => new Date(), []);
  const [viewMonth, setViewMonth] = useState<Date>(() =>
    startOfMonth(bookedDate ?? today),
  );

  const cells = useMemo(() => buildGrid(viewMonth), [viewMonth]);

  const monthLabel = viewMonth.toLocaleDateString(undefined, {
    month: "long",
    year: "numeric",
  });

  return (
    <div className="miniCal">
      <div className="miniCalHead">
        <button
          type="button"
          className="miniCalNav"
          aria-label="Previous month"
          onClick={() => setViewMonth((m) => addMonths(m, -1))}
        >
          ‹
        </button>
        <div className="miniCalLabel">{monthLabel}</div>
        <button
          type="button"
          className="miniCalNav"
          aria-label="Next month"
          onClick={() => setViewMonth((m) => addMonths(m, 1))}
        >
          ›
        </button>
      </div>
      <div className="miniCalWeek">
        {WEEKDAYS.map((w) => (
          <span key={w}>{w}</span>
        ))}
      </div>
      <div className="miniCalGrid">
        {cells.map((cell) => {
          const isToday = isSameDay(cell.date, today);
          const isBooked = bookedDate ? isSameDay(cell.date, bookedDate) : false;
          const cls = [
            "miniCalDay",
            cell.inMonth ? "" : "miniCalDay--muted",
            isToday ? "miniCalDay--today" : "",
            isBooked ? "miniCalDay--booked" : "",
          ]
            .filter(Boolean)
            .join(" ");
          return (
            <div key={cell.date.toISOString()} className={cls}>
              {cell.date.getDate()}
            </div>
          );
        })}
      </div>
    </div>
  );
}
