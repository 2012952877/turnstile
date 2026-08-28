import { CopilotLogo } from "../../components/brand-logos"
import { Button } from "../../components/ui/button"
import { copilotApi } from "./api"

function currentReturnPath() {
  const url = new URL(window.location.href)
  url.searchParams.set("source", "github-copilot")
  url.searchParams.delete("github_linked")
  url.searchParams.delete("github_error")
  return `${url.pathname}${url.search}${url.hash}`
}

export function CopilotConnectButton({
  purpose = "identity",
  organization,
}: {
  purpose?: "identity" | "organization"
  organization?: string
}) {
  const organizationConnection = purpose === "organization"
  return <Button
    type="button"
    className="copilot-connect-button"
    disabled={organizationConnection && !organization?.trim()}
    onClick={() => window.location.assign(
      copilotApi.oauthLoginUrl(
        currentReturnPath(),
        purpose,
        organization?.trim().toLowerCase(),
      ),
    )}
  >
    <CopilotLogo size={16} />{organizationConnection
      ? "通过 GitHub 授权连接组织数据"
      : "前往 GitHub 登录并授权"}
  </Button>
}
