// Register the Gerrit server for the Gerrit Trigger plugin (idempotent).
import com.sonyericsson.hudson.plugins.gerrit.trigger.PluginImpl
import com.sonyericsson.hudson.plugins.gerrit.trigger.GerritServer
import com.sonyericsson.hudson.plugins.gerrit.trigger.config.Config

def plugin = PluginImpl.getInstance()
if (plugin.getServer("gerrit") == null) {
    def server = new GerritServer("gerrit")
    def cfg = new Config()
    cfg.setGerritHostName(System.getenv("GERRIT_INTERNAL_HOST") ?: "gerrit")
    cfg.setGerritSshPort((System.getenv("GERRIT_SSH_PORT") ?: "29418") as int)
    cfg.setGerritUserName("jenkins")
    cfg.setGerritAuthKeyFile(new File("/var/jenkins_home/secrets-in/jenkins_ssh"))
    cfg.setGerritFrontEndURL(System.getenv("GERRIT_FRONTEND_URL"))
    server.setConfig(cfg)
    plugin.addServer(server)
    server.start()
    server.startConnection()
}
