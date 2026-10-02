import { Bell, Command, Search, Sparkles } from "lucide-react";

export function Topbar() {
  return (
    <header className="topbar">
      <div className="searchBox">
        <Search size={18} />
        <input
          placeholder="Search accounts, contacts, opportunities..."
          aria-label="Global search"
        />
        <div className="shortcut">
          <Command size={13} />
          K
        </div>
      </div>

      <div className="topbarActions">
        <button className="aiButton" type="button">
          <Sparkles size={17} />
          Ask AIVA
        </button>

        <button className="iconButton" type="button">
          <Bell size={19} />
          <span className="notificationDot" />
        </button>
      </div>
    </header>
  );
}
