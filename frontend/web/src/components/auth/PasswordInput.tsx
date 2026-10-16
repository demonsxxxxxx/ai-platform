import { Button, Input } from "@lobehub/ui/base-ui";
import { useState, type ChangeEvent } from "react";
import { Lock, Eye, EyeOff } from "lucide-react";
import { useTranslation } from "react-i18next";

interface PasswordInputProps {
  id?: string;
  value: string;
  onChange: (e: ChangeEvent<HTMLInputElement>) => void;
  placeholder?: string;
  autoComplete?: string;
  className?: string;
  showPasswordLabel?: string;
  hidePasswordLabel?: string;
}

export function PasswordInput({
  id,
  value,
  onChange,
  placeholder,
  autoComplete,
  className = "",
  showPasswordLabel,
  hidePasswordLabel,
}: PasswordInputProps) {
  const { t } = useTranslation();
  const showLabel = showPasswordLabel ?? t("passwordInput.showPassword");
  const hideLabel = hidePasswordLabel ?? t("passwordInput.hidePassword");
  const [visible, setVisible] = useState(false);

  return (
    <Input
      id={id}
      type={visible ? "text" : "password"}
      value={value}
      onChange={onChange}
      className={`w-full [&_::-ms-reveal]:hidden [&_::-webkit-credentials-auto-fill-button]:hidden ${className}`}
      prefix={<Lock size={15} />}
      suffix={<Button type="text" size="small" htmlType="button"
        onClick={() => setVisible(!visible)}
        aria-label={visible ? hideLabel : showLabel}
        icon={visible ? <EyeOff size={15} /> : <Eye size={15} />}
      />}
      aria-label={t("auth.password")}
      placeholder={placeholder}
      autoComplete={autoComplete}
    />
  );
}
