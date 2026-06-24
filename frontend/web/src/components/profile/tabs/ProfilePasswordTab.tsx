import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Eye, EyeOff, Check, AlertCircle } from "lucide-react";
import { authApi } from "../../../services/api";
import { LoadingSpinner } from "../../common/LoadingSpinner";

export function ProfilePasswordTab() {
  const { t } = useTranslation();
  const [isLoading, setIsLoading] = useState(false);
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [passwordError, setPasswordError] = useState("");
  const [passwordSuccess, setPasswordSuccess] = useState(false);

  const handlePasswordChange = async () => {
    setPasswordError("");
    setPasswordSuccess(false);

    if (!oldPassword || !newPassword || !confirmPassword) {
      setPasswordError(
        t("profile.oldPassword") +
          ", " +
          t("profile.newPassword") +
          ", " +
          t("profile.confirmPassword") +
          " required",
      );
      return;
    }

    if (newPassword !== confirmPassword) {
      setPasswordError(t("auth.validation.passwordMismatch"));
      return;
    }

    if (newPassword.length < 6) {
      setPasswordError(t("auth.validation.passwordMinLength"));
      return;
    }

    setIsLoading(true);
    try {
      await authApi.changePassword(oldPassword, newPassword);
      setPasswordSuccess(true);
      setOldPassword("");
      setNewPassword("");
      setConfirmPassword("");
    } catch (error) {
      setPasswordError(
        (error as Error).message || t("profile.passwordChangeFailed"),
      );
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="space-y-4">
      {passwordSuccess && (
        <div className="es-callout es-callout--success">
          <Check size={16} className="shrink-0" />
          <span className="text-sm">{t("profile.passwordChanged")}</span>
        </div>
      )}

      {passwordError && (
        <div className="es-error">
          <AlertCircle size={16} className="shrink-0" />
          <span>{passwordError}</span>
        </div>
      )}

      {/* Old Password */}
      <div>
        <label className="mb-1.5 block text-sm font-medium text-[var(--theme-text-secondary)]">
          {t("profile.oldPassword")}
        </label>
        <div className="relative">
          <input
            type={showPassword ? "text" : "password"}
            value={oldPassword}
            onChange={(e) => setOldPassword(e.target.value)}
            className="enterprise-form-input pr-10"
            placeholder={t("profile.oldPassword")}
          />
          <button
            type="button"
            onClick={() => setShowPassword(!showPassword)}
            className="btn-icon absolute right-2 top-1/2 h-8 w-8 -translate-y-1/2"
            aria-label={
              showPassword
                ? t("passwordInput.hidePassword")
                : t("passwordInput.showPassword")
            }
          >
            {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
          </button>
        </div>
      </div>

      {/* New Password */}
      <div>
        <label className="mb-1.5 block text-sm font-medium text-[var(--theme-text-secondary)]">
          {t("profile.newPassword")}
        </label>
        <input
          type={showPassword ? "text" : "password"}
          value={newPassword}
          onChange={(e) => setNewPassword(e.target.value)}
          className="enterprise-form-input"
          placeholder={t("profile.newPassword")}
        />
      </div>

      {/* Confirm Password */}
      <div>
        <label className="mb-1.5 block text-sm font-medium text-[var(--theme-text-secondary)]">
          {t("profile.confirmPassword")}
        </label>
        <input
          type={showPassword ? "text" : "password"}
          value={confirmPassword}
          onChange={(e) => setConfirmPassword(e.target.value)}
          className="enterprise-form-input"
          placeholder={t("profile.confirmPassword")}
        />
      </div>

      {/* Submit Button */}
      <button
        onClick={handlePasswordChange}
        disabled={isLoading}
        className="btn-primary w-full justify-center py-2.5"
      >
        <span className="inline-flex h-4 w-4 items-center justify-center">
          {isLoading ? <LoadingSpinner size="sm" color="text-white" /> : null}
        </span>
        <span>{t("profile.changePassword")}</span>
      </button>
    </div>
  );
}
