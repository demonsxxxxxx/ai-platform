import {
  Button, Input, ModalBackdrop, ModalClose, ModalContent, ModalDescription,
  ModalFooter, ModalHeader, ModalPopup, ModalPortal, ModalRoot, ModalTitle,
} from "@lobehub/ui/base-ui";
import { Eye, EyeOff, KeyRound, LockKeyhole, ShieldCheck } from "lucide-react";
import { useCallback, useRef, useState, type FormEvent, type RefObject } from "react";

import {
  PROFILE_DRIVE_CONNECTION_CHANGED_EVENT,
  profileDriveApi,
  ProfileDriveRequestError,
} from "../../services/api/profileDrive";

function profileDriveErrorMessage(error: unknown): string {
  if (error instanceof ProfileDriveRequestError) {
    if (error.code === "credential_rejected") {
      return "企业账号或密码不正确。";
    }
    if (error.code === "profile_drive_not_configured") {
      return "当前用户尚未配置本地文件服务器。";
    }
    if (error.code === "drive_unavailable") {
      return "文件服务器暂不可用，请稍后重试。";
    }
  }
  return "认证失败，请稍后重试。";
}

export function ProfileDriveAuthenticationDialog({
  open,
  onClose,
  onAuthenticated,
  returnFocusRef,
  fallbackFocusRef,
}: {
  open: boolean;
  onClose: () => void;
  onAuthenticated: () => void;
  returnFocusRef: RefObject<HTMLButtonElement | null>;
  fallbackFocusRef?: RefObject<HTMLButtonElement | null>;
}) {
  const passwordRef = useRef<HTMLInputElement>(null);
  const submittingRef = useRef(false);
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const resetAndClose = useCallback(() => {
    setPassword("");
    setShowPassword(false);
    setError(null);
    setIsSubmitting(false);
    onClose();
  }, [onClose]);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submittingRef.current) return;
    if (!password) {
      setError("请输入企业账号密码。");
      return;
    }

    submittingRef.current = true;
    setIsSubmitting(true);
    setError(null);
    try {
      const result = await profileDriveApi.connect(password);
      if (!result.connected) {
        setError("认证未建立文件服务器连接。");
        return;
      }
      onAuthenticated();
      window.dispatchEvent(new Event(PROFILE_DRIVE_CONNECTION_CHANGED_EVENT));
      resetAndClose();
    } catch (submissionError) {
      setError(profileDriveErrorMessage(submissionError));
    } finally {
      submittingRef.current = false;
      setIsSubmitting(false);
    }
  };

  return (
    <ModalRoot open={open} onOpenChange={(nextOpen, details) => {
      if (!nextOpen && submittingRef.current) details.cancel();
      else if (!nextOpen) resetAndClose();
    }}>
      <ModalPortal>
        <ModalBackdrop />
        <ModalPopup width={448} initialFocus={passwordRef}
          finalFocus={() => returnFocusRef.current ?? fallbackFocusRef?.current}>
          <ModalHeader>
            <ModalTitle>认证本地文件服务器</ModalTitle>
            <ModalClose aria-label="关闭本地文件服务器认证" disabled={isSubmitting} />
          </ModalHeader>
          <form autoComplete="off" onSubmit={handleSubmit}>
            <ModalContent className="space-y-5">
              <ModalDescription className="text-sm leading-6 text-[var(--theme-text-secondary)]">
                使用当前登录账号建立个人 Desktop 的受控连接。
              </ModalDescription>
              <div>
                <label htmlFor="file-server-password" className="mb-2 block text-sm font-medium">
                  密码
                </label>
                <Input
                  ref={passwordRef}
                  id="file-server-password"
                  size="large"
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(event) => {
                    setPassword(event.target.value);
                    setError(null);
                  }}
                  prefix={<LockKeyhole aria-hidden="true" size={18} />}
                  suffix={<Button type="text" htmlType="button"
                    aria-label={showPassword ? "隐藏密码" : "显示密码"}
                    onClick={() => setShowPassword((visible) => !visible)}
                    icon={showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
                  />}
                  aria-invalid={Boolean(error)}
                  aria-describedby={error ? "file-server-password-error" : undefined}
                  autoComplete="off"
                  placeholder="输入域账号密码"
                />
              </div>
              {error ? (
                <p id="file-server-password-error" role="alert" className="text-sm text-[var(--theme-danger-text)]">
                  {error}
                </p>
              ) : null}
              <p className="flex items-start gap-2 text-xs leading-5 text-[var(--theme-text-secondary)]">
                <ShieldCheck aria-hidden="true" className="mt-0.5 shrink-0" size={16} />
                <span>密码只用于建立当前会话，不会保存，也不会提供给 AI。</span>
              </p>
            </ModalContent>
            <ModalFooter>
              <Button size="large" htmlType="button" disabled={isSubmitting} onClick={resetAndClose}>
                取消
              </Button>
              <Button type="primary" size="large" htmlType="submit" disabled={isSubmitting}
                loading={isSubmitting} icon={<KeyRound size={16} />}>
                {isSubmitting ? "认证中..." : "提交认证"}
              </Button>
            </ModalFooter>
          </form>
        </ModalPopup>
      </ModalPortal>
    </ModalRoot>
  );
}
