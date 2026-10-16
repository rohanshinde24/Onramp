import "server-only";

import { cache } from "react";

import { get } from "./api";
import type { Me } from "./types";

/** The signed-in user, read once per request however many components ask. */
export const getMe = cache(() => get<Me>("/me"));
