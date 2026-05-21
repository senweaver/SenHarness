"use client";

import { useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import type {
  PlatformSettingSection,
  PlatformSettingsField,
  PlatformSettingsSchema,
} from "@/types/api";

import { EnvOverrideBadge } from "./EnvOverrideBadge";

interface FieldDef {
  name: string;
  schema: PlatformSettingsField;
  required: boolean;
}

function flattenAnyOf(field: PlatformSettingsField): PlatformSettingsField {
  // pydantic emits ``anyOf: [{type: "string"}, {type: "null"}]`` for
  // optional strings; drop the null variant so the form picks a concrete
  // input type. Composite anyOf (multiple non-null types) falls through
  // to a generic text input which is acceptable for the placeholder
  // sections (evolver / plugins).
  if (!field.anyOf) return field;
  const non_null = field.anyOf.filter((v) => v.type !== "null");
  if (non_null.length === 1) return { ...field, ...non_null[0], anyOf: undefined };
  return field;
}

function inferType(field: PlatformSettingsField): string {
  if (field.enum) return "enum";
  if (Array.isArray(field.type)) {
    return field.type.find((t) => t !== "null") ?? "string";
  }
  if (field.type) return field.type;
  return "string";
}

function buildFields(schema: PlatformSettingsSchema): FieldDef[] {
  const required = new Set(schema.required ?? []);
  return Object.entries(schema.properties ?? {}).map(([name, raw]) => ({
    name,
    schema: flattenAnyOf(raw),
    required: required.has(name),
  }));
}

export function SectionForm({
  section,
  schema,
  onSave,
  onReset,
  saving,
  resetting,
}: {
  section: PlatformSettingSection;
  schema: PlatformSettingsSchema;
  onSave: (
    value: Record<string, unknown>,
    confirmedDangerous: boolean,
  ) => Promise<unknown>;
  onReset: () => void;
  saving?: boolean;
  resetting?: boolean;
}) {
  const t = useTranslations("platformSettings");
  const tFields = useTranslations(`platformSettings.fields.${section.section}`);
  const fields = useMemo(() => buildFields(schema), [schema]);
  const [values, setValues] = useState<Record<string, unknown>>(section.value);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    setValues(section.value);
    setDirty(false);
  }, [section]);

  const update = (name: string, value: unknown) => {
    setValues((prev) => ({ ...prev, [name]: value }));
    setDirty(true);
  };

  const overrideSet = new Set(section.env_overrides);

  const safeFieldLabel = (key: string) => {
    try {
      const label = tFields(`${key}.label`);
      if (label && label.length > 0) return label;
    } catch {
      // Fall through.
    }
    return key;
  };

  const safeFieldHelp = (key: string) => {
    try {
      const help = tFields(`${key}.description`);
      if (help) return help;
    } catch {
      // Fall through.
    }
    return undefined;
  };

  return (
    <form
      className="space-y-5"
      onSubmit={async (e) => {
        e.preventDefault();
        await onSave(values, false);
      }}
    >
      <div className="space-y-4">
        {fields.map((field) => (
          <FieldRow
            key={field.name}
            field={field}
            value={values[field.name]}
            onChange={(v) => update(field.name, v)}
            envOverride={overrideSet.has(field.name)}
            label={safeFieldLabel(field.name)}
            help={safeFieldHelp(field.name)}
          />
        ))}
      </div>

      <div className="flex items-center gap-2 border-t pt-4">
        <Button type="submit" disabled={!dirty || saving}>
          {t("saveButton")}
        </Button>
        <Button
          type="button"
          variant="outline"
          onClick={onReset}
          disabled={resetting}
        >
          {t("resetButton")}
        </Button>
        {dirty && (
          <span className="text-[12px] text-amber-600 dark:text-amber-400">
            {t("unsavedChanges")}
          </span>
        )}
      </div>
    </form>
  );
}

function FieldRow({
  field,
  value,
  onChange,
  envOverride,
  label,
  help,
}: {
  field: FieldDef;
  value: unknown;
  onChange: (value: unknown) => void;
  envOverride: boolean;
  label: string;
  help?: string;
}) {
  const t = useInferType(field.schema);

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <Label className="text-sm font-medium">
          {label}
          {field.required && <span className="text-red-500"> *</span>}
        </Label>
        {envOverride && <EnvOverrideBadge />}
      </div>
      {renderInput(t, field, value, onChange)}
      {(help || field.schema.description) && (
        <p className="text-[11px] sh-muted">{help ?? field.schema.description}</p>
      )}
    </div>
  );
}

function useInferType(schema: PlatformSettingsField) {
  return inferType(schema);
}

function renderInput(
  type: string,
  field: FieldDef,
  value: unknown,
  onChange: (value: unknown) => void,
) {
  if (type === "enum" && field.schema.enum) {
    const options = field.schema.enum.map((v) => String(v));
    return (
      <Select
        value={String(value ?? "")}
        onValueChange={(v) => onChange(v)}
      >
        <SelectTrigger>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((o) => (
            <SelectItem key={o} value={o}>
              {o}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    );
  }
  if (type === "boolean") {
    return (
      <Switch
        checked={Boolean(value)}
        onCheckedChange={(v) => onChange(v)}
      />
    );
  }
  if (type === "integer" || type === "number") {
    return (
      <Input
        type="number"
        value={value === null || value === undefined ? "" : Number(value)}
        min={field.schema.minimum}
        max={field.schema.maximum}
        onChange={(e) => {
          const raw = e.target.value;
          if (raw === "") onChange(null);
          else onChange(Number(raw));
        }}
      />
    );
  }
  if (type === "array") {
    // Render the JSON for now; the placeholder sections (evolver / plugins)
    // never expose array fields, but ``auth.oauth.providers`` does and the
    // operator can edit it as JSON until M3 SSO ships a richer editor.
    return (
      <textarea
        className="min-h-[120px] w-full rounded-md border bg-transparent p-2 font-mono text-[12px]"
        value={JSON.stringify(value ?? [], null, 2)}
        onChange={(e) => {
          try {
            onChange(JSON.parse(e.target.value));
          } catch {
            // Keep raw text in field; don't blow away typing in progress.
          }
        }}
      />
    );
  }
  // Strings + email + everything else.
  return (
    <Input
      type={field.schema.format === "email" ? "email" : "text"}
      value={value === null || value === undefined ? "" : String(value)}
      onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)}
      pattern={field.schema.pattern}
      maxLength={field.schema.maxLength}
      placeholder={field.schema.description ?? undefined}
    />
  );
}
