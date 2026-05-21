"use client";

import { useState } from "react";
import { IconPlus, IconX } from "@tabler/icons-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

interface ChipListFieldProps {
  label: string;
  hint?: string;
  placeholder?: string;
  values: string[];
  onChange: (next: string[]) => void;
  className?: string;
}

export function ChipListField({
  label,
  hint,
  placeholder,
  values,
  onChange,
  className,
}: ChipListFieldProps) {
  const [draft, setDraft] = useState("");

  const add = () => {
    const trimmed = draft.trim();
    if (!trimmed) return;
    if (values.includes(trimmed)) return;
    onChange([...values, trimmed]);
    setDraft("");
  };

  const remove = (entry: string) => onChange(values.filter((v) => v !== entry));

  return (
    <div className={cn("space-y-1.5", className)}>
      <Label className="text-[12px]">{label}</Label>
      <div className="flex gap-2">
        <Input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={placeholder}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }}
        />
        <Button type="button" size="sm" onClick={add}>
          <IconPlus className="size-3.5" />
        </Button>
      </div>
      {values.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {values.map((entry) => (
            <Badge key={entry} variant="outline" className="gap-1 pl-2 pr-1">
              <span className="font-mono text-[11px]">{entry}</span>
              <button
                type="button"
                onClick={() => remove(entry)}
                className="rounded p-0.5 hover:bg-black/10 dark:hover:bg-white/10"
                aria-label="remove"
              >
                <IconX className="size-3" />
              </button>
            </Badge>
          ))}
        </div>
      )}
      {hint && <p className="text-[11px] sh-muted">{hint}</p>}
    </div>
  );
}
