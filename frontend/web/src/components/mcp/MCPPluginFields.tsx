import { Checkbox, Input, TextArea } from "@lobehub/ui/base-ui";
import { useTranslation } from "react-i18next";
import type { MCPIntegrationKind, MCPPluginConfiguration } from "../../types/mcp";
import { LobeSelectField } from "../lobe-ui/LobeSelectField";

export function MCPPluginFields({ value, onChange, toolNames, disabled, integrationKind }: {
  value: MCPPluginConfiguration;
  onChange: (value: MCPPluginConfiguration) => void;
  toolNames: string[];
  disabled: boolean;
  integrationKind: MCPIntegrationKind;
}) {
  const { t } = useTranslation();
  const defaults = value.default_tools ?? [];
  const names = Array.from(new Set([...toolNames, ...defaults]));
  return <fieldset className="space-y-3" disabled={disabled}>
    <legend className="es-label">{t("plugins.settings.title")}</legend>
    <label className="es-field">{t("plugins.settings.displayName")}
      <Input value={value.display_name} maxLength={128} onChange={e => onChange({ ...value, display_name: e.target.value })} />
    </label>
    <label className="es-field">{t("plugins.settings.description")}
      <TextArea value={value.description} maxLength={1000} onChange={e => onChange({ ...value, description: e.target.value })} />
    </label>
    <div className="es-field"><span className="es-label">{t("plugins.settings.authorization")}</span>
      <LobeSelectField aria-label={t("plugins.settings.authorization")} value={value.user_authorization} disabled={disabled}
        onChange={mode => onChange({ ...value, user_authorization: mode as MCPPluginConfiguration["user_authorization"] })}
        options={[{ value: "none", label: t("plugins.settings.noAdditionalAuthorization") }, { value: "profile_drive", label: t("plugins.settings.profileDrive") }]} />
    </div>
    {integrationKind !== "enterprise_gateway" ? <div className="space-y-2" role="group" aria-label={t("plugins.settings.defaults")}>
      <h3 className="es-label">{t("plugins.settings.defaults")}</h3>
      <p className="es-hint">{t("plugins.settings.defaultsHint")}</p>
      {names.map(name => <label key={name} className="flex items-center gap-2 break-all text-sm">
        <Checkbox checked={defaults.includes(name)} disabled={disabled}
          onChange={checked => onChange({ ...value, default_tools: checked
            ? [...defaults, name] : defaults.filter(tool => tool !== name) })} />{name}
      </label>)}
      {!names.length ? <p className="es-hint">{t("plugins.settings.saveAndDiscover")}</p> : null}
    </div> : null}
  </fieldset>;
}
