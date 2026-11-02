import { useId, useState, type FormEvent } from "react";

import { ApiError } from "../lib/api";
import type { Profile, ProfileInput } from "../types/api";
import { Dialog, errorMessage } from "./shared";

const PROFILE_ID = /^[a-z0-9][a-z0-9_-]{0,63}$/;

type Errors = Partial<Record<"name" | "profile_id" | "sources" | "target", string>>;

/** Lines entered in a textarea, trimmed, with blank lines dropped. */
function lines(value: string): string[] {
  return value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

/** Accept POSIX, Windows drive, and UNC absolute paths; the API makes the final decision. */
export function isAbsolutePath(path: string): boolean {
  return /^(\/|[A-Za-z]:[\\/]|\\\\[^\\]+\\)/.test(path);
}

export function slugify(name: string): string {
  return name
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64);
}

export function validateProfile(input: ProfileInput, existingIds: string[], isNew: boolean): Errors {
  const errors: Errors = {};
  if (!input.name.trim()) errors.name = "Enter a name for this profile.";
  if (isNew) {
    if (!PROFILE_ID.test(input.profile_id)) {
      errors.profile_id =
        "Use lowercase letters, digits, hyphens, or underscores, starting with a letter or digit.";
    } else if (existingIds.includes(input.profile_id)) {
      errors.profile_id = "Another profile already uses this ID.";
    }
  }
  if (input.sources.length === 0) {
    errors.sources = "Add at least one folder to back up.";
  } else {
    const relative = input.sources.find((source) => !isAbsolutePath(source));
    if (relative) errors.sources = `Use a full path, for example C:\\Users\\you\\Documents: ${relative}`;
  }
  if (!input.target.trim()) {
    errors.target = "Enter the folder where snapshots will be stored.";
  } else if (!isAbsolutePath(input.target)) {
    errors.target = "Use a full path, for example E:\\Backups\\home.";
  }
  return errors;
}

export function ProfileForm({
  profile,
  existingIds,
  onCancel,
  onSubmit,
}: {
  /** The profile to edit, or null to create a new one. */
  profile: Profile | null;
  existingIds: string[];
  onCancel: () => void;
  onSubmit: (input: ProfileInput) => Promise<void>;
}) {
  const isNew = profile === null;
  const uid = useId();
  const fieldId = (field: string) => `${uid}-${field}`;
  const [name, setName] = useState(profile?.name ?? "");
  const [profileId, setProfileId] = useState(profile?.profile_id ?? "");
  const [idEdited, setIdEdited] = useState(!isNew);
  const [sources, setSources] = useState(profile?.sources.join("\n") ?? "");
  const [target, setTarget] = useState(profile?.target ?? "");
  const [excludes, setExcludes] = useState(profile?.excludes.join("\n") ?? "");
  const [errors, setErrors] = useState<Errors>({});
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const effectiveId = idEdited ? profileId : slugify(name);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const input: ProfileInput = {
      profile_id: effectiveId,
      name: name.trim(),
      sources: lines(sources),
      target: target.trim(),
      excludes: lines(excludes),
    };
    const found = validateProfile(input, existingIds, isNew);
    setErrors(found);
    setSubmitError(null);
    if (Object.keys(found).length > 0) return;

    setSaving(true);
    try {
      await onSubmit(input);
    } catch (error) {
      if (error instanceof ApiError && error.code === "PROFILE_EXISTS") {
        setErrors({ profile_id: "Another profile already uses this ID." });
      } else {
        setSubmitError(errorMessage(error));
      }
      setSaving(false);
    }
  };

  const fieldError = (field: keyof Errors) =>
    errors[field] ? (
      <p className="field-error" id={fieldId(`${field}-error`)}>
        {errors[field]}
      </p>
    ) : null;
  const describedBy = (field: keyof Errors, hint?: string) =>
    [hint && fieldId(hint), errors[field] ? fieldId(`${field}-error`) : null].filter(Boolean).join(" ") || undefined;

  return (
    <Dialog
      title={isNew ? "New backup profile" : `Edit ${profile.name}`}
      description="Profiles describe what to back up and where snapshots are stored."
      onClose={saving ? () => undefined : onCancel}
      wide
    >
      <form className="profile-form" onSubmit={(event) => void handleSubmit(event)} noValidate>
        <div className="form-grid">
          <div className="field">
            <label className="field-label" htmlFor={fieldId("name")}>Name</label>
            <input
              id={fieldId("name")}
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Home documents"
              aria-invalid={Boolean(errors.name)}
              aria-describedby={describedBy("name")}
              autoComplete="off"
            />
            {fieldError("name")}
          </div>
          <div className="field">
            <label className="field-label" htmlFor={fieldId("profile_id")}>Profile ID</label>
            <input
              id={fieldId("profile_id")}
              value={effectiveId}
              onChange={(event) => {
                setIdEdited(true);
                setProfileId(event.target.value);
              }}
              disabled={!isNew}
              placeholder="home-documents"
              aria-invalid={Boolean(errors.profile_id)}
              aria-describedby={describedBy("profile_id", "profile-id-hint")}
              autoComplete="off"
              spellCheck={false}
            />
            <span className="field-hint" id={fieldId("profile-id-hint")}>
              {isNew ? "Permanent identifier, generated from the name." : "The ID cannot be changed."}
            </span>
            {fieldError("profile_id")}
          </div>
        </div>

        <div className="field">
          <label className="field-label" htmlFor={fieldId("sources")}>Folders to back up</label>
          <textarea
            id={fieldId("sources")}
            value={sources}
            onChange={(event) => setSources(event.target.value)}
            rows={3}
            placeholder={"C:\\Users\\you\\Documents\nC:\\Users\\you\\Pictures"}
            aria-invalid={Boolean(errors.sources)}
            aria-describedby={describedBy("sources", "sources-hint")}
            spellCheck={false}
          />
          <span className="field-hint" id={fieldId("sources-hint")}>One full path per line.</span>
          {fieldError("sources")}
        </div>

        <div className="field">
          <label className="field-label" htmlFor={fieldId("target")}>Snapshot destination</label>
          <input
            id={fieldId("target")}
            value={target}
            onChange={(event) => setTarget(event.target.value)}
            placeholder="E:\Backups\home"
            aria-invalid={Boolean(errors.target)}
            aria-describedby={describedBy("target", "target-hint")}
            spellCheck={false}
          />
          <span className="field-hint" id={fieldId("target-hint")}>
            Must not be inside a folder you are backing up. Usually an external or second drive.
          </span>
          {fieldError("target")}
        </div>

        <div className="field">
          <label className="field-label" htmlFor={fieldId("excludes")}>Exclude rules <span className="optional">optional</span></label>
          <textarea
            id={fieldId("excludes")}
            value={excludes}
            onChange={(event) => setExcludes(event.target.value)}
            rows={3}
            placeholder={"*.tmp\nnode_modules/"}
            aria-describedby={fieldId("excludes-hint")}
            spellCheck={false}
          />
          <span className="field-hint" id={fieldId("excludes-hint")}>
            One pattern per line. Matching files and folders are skipped.
          </span>
        </div>

        {submitError && (
          <div className="form-alert" role="alert">
            {submitError}
          </div>
        )}

        <footer className="dialog-actions">
          <button type="button" className="secondary-button" onClick={onCancel} disabled={saving}>
            Cancel
          </button>
          <button type="submit" className="primary-button" disabled={saving} aria-busy={saving}>
            {saving ? "Saving…" : isNew ? "Create profile" : "Save changes"}
          </button>
        </footer>
      </form>
    </Dialog>
  );
}

export function DeleteProfileDialog({
  profile,
  onCancel,
  onConfirm,
}: {
  profile: Profile;
  onCancel: () => void;
  onConfirm: () => Promise<void>;
}) {
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const confirm = async () => {
    setDeleting(true);
    setError(null);
    try {
      await onConfirm();
    } catch (failure) {
      setError(errorMessage(failure));
      setDeleting(false);
    }
  };

  return (
    <Dialog title={`Remove ${profile.name}?`} onClose={deleting ? () => undefined : onCancel}>
      <div className="dialog-body">
        <p>
          This removes the profile from your configuration. Snapshots already stored in{" "}
          <code className="inline-path">{profile.target}</code> are <strong>not deleted</strong>{" "}
          and stay on disk.
        </p>
        {error && (
          <div className="form-alert" role="alert">
            {error}
          </div>
        )}
      </div>
      <footer className="dialog-actions">
        <button type="button" className="secondary-button" onClick={onCancel} disabled={deleting}>
          Cancel
        </button>
        <button
          type="button"
          className="danger-button"
          onClick={() => void confirm()}
          disabled={deleting}
          aria-busy={deleting}
        >
          {deleting ? "Removing…" : "Remove profile"}
        </button>
      </footer>
    </Dialog>
  );
}
