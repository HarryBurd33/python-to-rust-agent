use std::{env, ffi::OsString, process::ExitCode};

const MAX_COUNT: u32 = 3_000_000;

fn compute(args: &[OsString]) -> Result<u32, &'static str> {
    let [count] = args else {
        return Err("expected one count");
    };
    if count.is_empty() {
        return Err("count is empty");
    }
    if !count.as_encoded_bytes().iter().all(u8::is_ascii_digit) {
        return Err("count must use ASCII digits");
    }
    let count = count.to_str().ok_or("count must use ASCII digits")?;
    let count = count.trim_start_matches('0');
    let count = if count.is_empty() {
        0
    } else {
        count
            .parse::<u32>()
            .ok()
            .filter(|count| *count <= MAX_COUNT)
            .ok_or("count exceeds 3000000")?
    };

    let mut value = 1u32;
    for index in 0..count {
        value = value
            .wrapping_mul(1_664_525)
            .wrapping_add(1_013_904_223)
            .wrapping_add(index);
    }
    Ok(value)
}

fn main() -> ExitCode {
    let args: Vec<OsString> = env::args_os().skip(1).collect();
    match compute(&args) {
        Ok(value) => {
            println!("{value}");
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("{error}");
            ExitCode::from(2)
        }
    }
}
