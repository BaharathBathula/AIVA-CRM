"use client";

import {
  useEffect,
  useMemo,
  useState,
} from "react";

import Link from "next/link";

import {
  Building2,
  Filter,
  Mail,
  Phone,
  Plus,
  Search,
  Star,
  UserCheck,
  Users,
} from "lucide-react";

import {
  CreateContactModal,
} from "@/components/contacts/create-contact-modal";

import {
  Sidebar,
} from "@/components/sidebar";

import {
  Topbar,
} from "@/components/topbar";

import {
  getAccounts,
} from "@/lib/accounts";

import {
  getContacts,
} from "@/lib/contacts";

import type {
  Account,
} from "@/types/account";

import type {
  Contact,
} from "@/types/contact";


type ContactFilter =
  | "all"
  | "primary"
  | "active"
  | "inactive";


const filters:
  ContactFilter[] = [
    "all",
    "primary",
    "active",
    "inactive",
  ];


function displayLabel(
  value: string
) {
  return value
    .replaceAll(
      "_",
      " "
    )
    .replace(
      /\b\w/g,
      (letter) =>
        letter.toUpperCase()
    );
}


function initials(
  contact: Contact
) {
  return (
    `${
      contact.first_name[0]
        ?? ""
    }${
      contact.last_name[0]
        ?? ""
    }`
      .toUpperCase()
  );
}


export default function ContactsPage() {
  const [
    contacts,
    setContacts,
  ] = useState<Contact[]>([]);

  const [
    accounts,
    setAccounts,
  ] = useState<Account[]>([]);

  const [
    loading,
    setLoading,
  ] = useState(true);

  const [
    error,
    setError,
  ] = useState<
    string | null
  >(null);

  const [
    query,
    setQuery,
  ] = useState("");

  const [
    filter,
    setFilter,
  ] = useState<ContactFilter>(
    "all"
  );

  const [
    createOpen,
    setCreateOpen,
  ] = useState(false);


  useEffect(() => {
    let cancelled =
      false;

    async function load() {
      try {
        setLoading(true);
        setError(null);

        const [
          contactsResult,
          accountsResult,
        ] = await Promise.all([
          getContacts(
            undefined,
            {
              limit: 100,
            }
          ),

          getAccounts(),
        ]);

        if (cancelled) {
          return;
        }

        setContacts(
          contactsResult
        );

        setAccounts(
          accountsResult
        );
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error
              ? err.message
              : "Unable to load contacts."
          );
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void load();

    return () => {
      cancelled = true;
    };
  }, []);


  const accountNames =
    useMemo(() => {
      return new Map(
        accounts.map(
          (account) => [
            account.id,
            account.name,
          ]
        )
      );
    }, [
      accounts,
    ]);


  const primaryCount =
    useMemo(
      () =>
        contacts.filter(
          (contact) =>
            contact.is_primary
        ).length,
      [
        contacts,
      ]
    );


  const activeCount =
    useMemo(
      () =>
        contacts.filter(
          (contact) =>
            contact.is_active
        ).length,
      [
        contacts,
      ]
    );


  const filteredContacts =
    useMemo(() => {
      const normalized =
        query
          .trim()
          .toLowerCase();

      return contacts.filter(
        (
          contact
        ) => {
          const matchesFilter =
            filter === "all"
            ||
            (
              filter
              === "primary"
              &&
              contact.is_primary
            )
            ||
            (
              filter
              === "active"
              &&
              contact.is_active
            )
            ||
            (
              filter
              === "inactive"
              &&
              !contact.is_active
            );

          const accountName =
            contact.account_id
              ? (
                  accountNames.get(
                    contact.account_id
                  )
                  ?? ""
                )
              : "";

          const searchable = [
            contact.first_name,
            contact.last_name,
            contact.email,
            contact.phone,
            contact.mobile,
            contact.job_title,
            contact.department,
            accountName,
          ]
            .filter(
              Boolean
            )
            .join(
              " "
            )
            .toLowerCase();

          const matchesSearch =
            !normalized
            ||
            searchable.includes(
              normalized
            );

          return (
            matchesFilter
            &&
            matchesSearch
          );
        }
      );
    }, [
      contacts,
      accountNames,
      filter,
      query,
    ]);


  function addCreatedContact(
    contact: Contact
  ) {
    setContacts(
      (
        current
      ) => [
        contact,
        ...current,
      ]
    );
  }


  return (
    <div className="appShell">
      <Sidebar
        active="Contacts"
      />

      <main className="mainArea">
        <Topbar />

        <div className="pageContent">
          <div className="pageHeading accountsHeading">
            <div>
              <p className="eyebrow">
                Sales
              </p>

              <h1>
                Contacts
              </h1>

              <p>
                Manage the people and
                relationships connected
                to your customer accounts.
              </p>
            </div>

            <button
              type="button"
              className="createButton"
              onClick={() =>
                setCreateOpen(
                  true
                )
              }
            >
              <Plus
                size={16}
              />

              New Contact
            </button>
          </div>


          <div className="accountSummary">
            <div>
              <Users
                size={18}
              />

              <span>
                Total Contacts
              </span>

              <strong>
                {
                  contacts.length
                }
              </strong>
            </div>

            <div>
              <Star
                size={18}
              />

              <span>
                Primary Contacts
              </span>

              <strong>
                {
                  primaryCount
                }
              </strong>
            </div>

            <div>
              <UserCheck
                size={18}
              />

              <span>
                Active Contacts
              </span>

              <strong>
                {
                  activeCount
                }
              </strong>
            </div>
          </div>


          <section className="accountsPanel">
            <div className="accountToolbar">
              <div className="accountSearch">
                <Search
                  size={17}
                />

                <input
                  value={
                    query
                  }
                  onChange={(
                    event
                  ) =>
                    setQuery(
                      event.target.value
                    )
                  }
                  placeholder={
                    "Search contacts, company, title..."
                  }
                />
              </div>

              <div className="accountFilters">
                <Filter
                  size={15}
                />

                {filters.map(
                  (
                    item
                  ) => (
                    <button
                      key={
                        item
                      }
                      type="button"
                      className={
                        filter
                        === item
                          ? "filterChip active"
                          : "filterChip"
                      }
                      onClick={() =>
                        setFilter(
                          item
                        )
                      }
                    >
                      {
                        displayLabel(
                          item
                        )
                      }
                    </button>
                  )
                )}
              </div>
            </div>


            {loading && (
              <div className="tableState">
                Loading contacts...
              </div>
            )}


            {error &&
              !loading && (
                <div className="tableState errorState">
                  <strong>
                    Contacts unavailable
                  </strong>

                  <span>
                    {error}
                  </span>
                </div>
              )}


            {!loading
              &&
              !error
              &&
              filteredContacts.length
                === 0 && (
                <div className="emptyState">
                  <div className="emptyStateIcon">
                    <Users
                      size={23}
                    />
                  </div>

                  <h3>
                    No contacts found
                  </h3>

                  <p>
                    Add your first
                    customer contact
                    to AIVA CRM.
                  </p>

                  <button
                    type="button"
                    className="createButton"
                    onClick={() =>
                      setCreateOpen(
                        true
                      )
                    }
                  >
                    <Plus
                      size={15}
                    />

                    New Contact
                  </button>
                </div>
              )}


            {!loading
              &&
              !error
              &&
              filteredContacts.length
                > 0 && (
                <div className="accountTableWrapper">
                  <table className="accountTable">
                    <thead>
                      <tr>
                        <th>
                          Contact
                        </th>

                        <th>
                          Account
                        </th>

                        <th>
                          Title
                        </th>

                        <th>
                          Email
                        </th>

                        <th>
                          Phone
                        </th>

                        <th>
                          Relationship
                        </th>

                        <th>
                          Status
                        </th>
                      </tr>
                    </thead>

                    <tbody>
                      {filteredContacts.map(
                        (
                          contact
                        ) => {
                          const accountName =
                            contact.account_id
                              ? (
                                  accountNames.get(
                                    contact.account_id
                                  )
                                  ?? "Unknown account"
                                )
                              : "No account";

                          return (
                            <tr
                              key={
                                contact.id
                              }
                            >
                              <td>
                                <Link
                                  href={
                                    `/contacts/${contact.id}`
                                  }
                                  className="accountIdentity"
                                >
                                  <div className="contactAvatar">
                                    {
                                      initials(
                                        contact
                                      )
                                    }
                                  </div>

                                  <div>
                                    <strong>
                                      {
                                        contact.first_name
                                      }{" "}
                                      {
                                        contact.last_name
                                      }
                                    </strong>

                                    <span>
                                      {
                                        contact.department
                                        || "Contact"
                                      }
                                    </span>
                                  </div>
                                </Link>
                              </td>


                              <td>
                                {contact.account_id ? (
                                  <Link
                                    href={
                                      `/accounts/${contact.account_id}`
                                    }
                                    style={{
                                      display:
                                        "inline-flex",
                                      alignItems:
                                        "center",
                                      gap:
                                        "5px",
                                      color:
                                        "var(--primary)",
                                      textDecoration:
                                        "none",
                                    }}
                                  >
                                    <Building2
                                      size={13}
                                    />

                                    {
                                      accountName
                                    }
                                  </Link>
                                ) : (
                                  "—"
                                )}
                              </td>


                              <td>
                                {
                                  contact.job_title
                                  || "—"
                                }
                              </td>


                              <td>
                                {contact.email ? (
                                  <a
                                    href={
                                      `mailto:${contact.email}`
                                    }
                                    style={{
                                      display:
                                        "inline-flex",
                                      alignItems:
                                        "center",
                                      gap:
                                        "5px",
                                      color:
                                        "var(--primary)",
                                      textDecoration:
                                        "none",
                                    }}
                                  >
                                    <Mail
                                      size={12}
                                    />

                                    {
                                      contact.email
                                    }
                                  </a>
                                ) : (
                                  "—"
                                )}
                              </td>


                              <td>
                                {(
                                  contact.mobile
                                  ||
                                  contact.phone
                                ) ? (
                                  <a
                                    href={
                                      `tel:${
                                        contact.mobile
                                        ||
                                        contact.phone
                                      }`
                                    }
                                    style={{
                                      display:
                                        "inline-flex",
                                      alignItems:
                                        "center",
                                      gap:
                                        "5px",
                                      color:
                                        "inherit",
                                      textDecoration:
                                        "none",
                                    }}
                                  >
                                    <Phone
                                      size={12}
                                    />

                                    {
                                      contact.mobile
                                      ||
                                      contact.phone
                                    }
                                  </a>
                                ) : (
                                  "—"
                                )}
                              </td>


                              <td>
                                {contact.is_primary ? (
                                  <span className="stageBadge stage-customer">
                                    Primary
                                  </span>
                                ) : (
                                  <span className="stageBadge stage-prospect">
                                    Standard
                                  </span>
                                )}
                              </td>


                              <td>
                                <span
                                  className={
                                    contact.is_active
                                      ? "stageBadge stage-customer"
                                      : "stageBadge stage-inactive"
                                  }
                                >
                                  {
                                    contact.is_active
                                      ? "Active"
                                      : "Inactive"
                                  }
                                </span>
                              </td>
                            </tr>
                          );
                        }
                      )}
                    </tbody>
                  </table>
                </div>
              )}
          </section>
        </div>
      </main>


      <CreateContactModal
        open={
          createOpen
        }
        onClose={() =>
          setCreateOpen(
            false
          )
        }
        onCreated={
          addCreatedContact
        }
      />
    </div>
  );
}